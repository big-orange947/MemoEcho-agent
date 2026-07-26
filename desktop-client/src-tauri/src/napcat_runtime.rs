use reqwest::blocking::Client;
use serde::Serialize;
use sha2::{Digest, Sha256};
use std::fs::{self, File, OpenOptions};
use std::io::{self, Read, Write};
use std::net::{IpAddr, Ipv4Addr, SocketAddr, TcpStream};
use std::path::{Path, PathBuf};
use std::process::{Command, Stdio};
use std::sync::{Mutex, OnceLock};
use std::thread;
use std::time::{Duration, Instant};
use tauri::{AppHandle, Manager};

#[cfg(target_os = "windows")]
use std::os::windows::process::CommandExt;

const NAPCAT_VERSION: &str = "v4.18.9";
const NAPCAT_ARCHIVE_URL: &str =
    "https://github.com/NapNeko/NapCatQQ/releases/download/v4.18.9/NapCat.Shell.Windows.Node.zip";
const NAPCAT_ARCHIVE_SHA256: &str =
    "234f2b9341d355d107881ce486d6699f529300d644282e25af452717d00a50da";
const NAPCAT_ARCHIVE_SIZE: u64 = 114_832_420;
const NAPCAT_LICENSE_URL: &str = "https://github.com/NapNeko/NapCatQQ/blob/main/LICENSE";
const NAPCAT_WEB_UI_URL: &str = "http://127.0.0.1:6099/webui";
const NAPCAT_WEB_UI_PORT: u16 = 6099;
const START_TIMEOUT: Duration = Duration::from_secs(120);
const LAST_ACCOUNT_FILE: &str = "last-account-id";

#[cfg(target_os = "windows")]
const CREATE_NO_WINDOW: u32 = 0x0800_0000;

/**
 * 读取 Windows 当前用户启用的 HTTP 代理。
 * GitHub 被本地加速工具接管时，显式复用用户代理通常比 hosts 转发链路稳定。
 */
#[cfg(target_os = "windows")]
fn windows_user_proxy() -> Option<String> {
    const INTERNET_SETTINGS: &str =
        r"HKCU\Software\Microsoft\Windows\CurrentVersion\Internet Settings";

    fn registry_value(key: &str, name: &str) -> Option<String> {
        let output = Command::new("reg.exe")
            .args(["query", key, "/v", name])
            .creation_flags(CREATE_NO_WINDOW)
            .output()
            .ok()?;
        if !output.status.success() {
            return None;
        }
        String::from_utf8_lossy(&output.stdout)
            .lines()
            .find(|line| line.contains(name))
            .and_then(|line| line.split_whitespace().last())
            .map(str::to_string)
    }

    let enabled = registry_value(INTERNET_SETTINGS, "ProxyEnable")?;
    if enabled != "0x1" && enabled != "1" {
        return None;
    }

    let configured = registry_value(INTERNET_SETTINGS, "ProxyServer")?;
    let server = if configured.contains('=') {
        configured
            .split(';')
            .find_map(|item| item.strip_prefix("https="))
            .or_else(|| {
                configured
                    .split(';')
                    .find_map(|item| item.strip_prefix("http="))
            })?
            .to_string()
    } else {
        configured
    };
    if server.contains("://") {
        Some(server)
    } else {
        Some(format!("http://{server}"))
    }
}

/**
 * 返回给前端的 NapCat 托管状态。
 * managed=false 表示检测到了用户自行启动的 NapCat，Memo Echo 只复用它，不会修改或停止它。
 */
#[derive(Debug, Clone, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct NapcatRuntimeStatus {
    pub state: String,
    pub installed: bool,
    pub running: bool,
    pub ready: bool,
    pub managed: bool,
    pub version: String,
    pub web_ui_url: String,
    pub message: String,
    pub license_url: String,
}

/**
 * 暴露给客户端的安装进度。
 * 下载、校验和解压分开建模，避免 110 MB 安装期间只能显示一个无限转圈动画。
 */
#[derive(Debug, Clone, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct NapcatInstallProgress {
    pub state: String,
    pub downloaded_bytes: u64,
    pub total_bytes: u64,
    pub percent: f64,
    pub bytes_per_second: u64,
    pub message: String,
    pub error: String,
}

static INSTALL_PROGRESS: OnceLock<Mutex<NapcatInstallProgress>> = OnceLock::new();

/** 返回安装进度的全局存储；锁中只保存轻量标量，不执行文件或网络操作。 */
fn install_progress_store() -> &'static Mutex<NapcatInstallProgress> {
    INSTALL_PROGRESS.get_or_init(|| {
        Mutex::new(NapcatInstallProgress {
            state: "IDLE".to_string(),
            downloaded_bytes: 0,
            total_bytes: NAPCAT_ARCHIVE_SIZE,
            percent: 0.0,
            bytes_per_second: 0,
            message: "尚未开始安装".to_string(),
            error: String::new(),
        })
    })
}

/** 读取进度快照；即使某个后台线程异常退出导致锁中毒，也保留最后一次有效状态。 */
fn install_progress_snapshot() -> NapcatInstallProgress {
    install_progress_store()
        .lock()
        .unwrap_or_else(|poisoned| poisoned.into_inner())
        .clone()
}

/** 集中更新安装阶段和传输指标，确保前端拿到的百分比始终处于 0 到 100。 */
fn update_install_progress(
    state: &str,
    downloaded_bytes: u64,
    total_bytes: u64,
    bytes_per_second: u64,
    message: impl Into<String>,
    error: impl Into<String>,
) {
    let total = total_bytes.max(1);
    let mut progress = install_progress_store()
        .lock()
        .unwrap_or_else(|poisoned| poisoned.into_inner());
    progress.state = state.to_string();
    progress.downloaded_bytes = downloaded_bytes;
    progress.total_bytes = total;
    progress.percent = ((downloaded_bytes as f64 / total as f64) * 100.0).clamp(0.0, 100.0);
    progress.bytes_per_second = bytes_per_second;
    progress.message = message.into();
    progress.error = error.into();
}

/** 获取当前应用专属的 NapCat 运行目录，避免把文件写入项目目录或系统目录。 */
fn runtime_root(app: &AppHandle) -> Result<PathBuf, String> {
    app.path()
        .app_local_data_dir()
        .map(|path| path.join("napcat-runtime"))
        .map_err(|error| format!("无法确定 NapCat 本地运行目录：{error}"))
}

/**
 * 校验可用于 NapCat 快速登录的 QQ 号。
 * 这里只允许纯数字账号，避免把来自数据库或前端的任意文本传给本地启动器。
 */
fn normalize_account_id(account_id: Option<&str>) -> Option<String> {
    let value = account_id?.trim();
    if (5..=12).contains(&value.len()) && value.chars().all(|character| character.is_ascii_digit())
    {
        Some(value.to_string())
    } else {
        None
    }
}

/** 读取最近一次成功登录的 QQ 号；文件损坏时按未记录处理，不阻断 NapCat 启动。 */
fn read_last_account(root: &Path) -> Option<String> {
    let value = fs::read_to_string(root.join(LAST_ACCOUNT_FILE)).ok()?;
    normalize_account_id(Some(&value))
}

/** 保存最近一次成功登录的 QQ 号，供下次启动直接复用 QQ 的本地登录会话。 */
fn write_last_account(root: &Path, account_id: &str) -> Result<String, String> {
    let account_id = normalize_account_id(Some(account_id))
        .ok_or_else(|| "QQ 账号格式无效，无法用于快速登录".to_string())?;
    fs::create_dir_all(root).map_err(|error| format!("创建 NapCat 本地目录失败：{error}"))?;
    fs::write(root.join(LAST_ACCOUNT_FILE), &account_id)
        .map_err(|error| format!("保存最近登录 QQ 账号失败：{error}"))?;
    Ok(account_id)
}

/** 探测 NapCat WebUI 端口；只做本机 TCP 连接，不发送 Token 或账号信息。 */
fn web_ui_ready() -> bool {
    let address = SocketAddr::new(IpAddr::V4(Ipv4Addr::LOCALHOST), NAPCAT_WEB_UI_PORT);
    TcpStream::connect_timeout(&address, Duration::from_millis(350)).is_ok()
}

/**
 * 在官方 Windows Node 资产中寻找 node.exe，同时兼容旧的 NapCat.*.Shell 安装目录。
 * Node 资产是 OneKey 引导器最终拉取的完整无头运行时，直接执行 index.js 可避免二次下载地址失效。
 */
fn find_managed_launcher(root: &Path) -> Option<PathBuf> {
    fn visit(directory: &Path, depth: usize) -> Option<PathBuf> {
        if depth > 5 {
            return None;
        }
        let node_launcher = directory.join("node.exe");
        if node_launcher.is_file()
            && directory.join("index.js").is_file()
            && directory
                .join("napcat")
                .join("NapCatWinBootMain.exe")
                .is_file()
        {
            return Some(node_launcher);
        }
        let entries = fs::read_dir(directory).ok()?;
        for entry in entries.flatten() {
            let path = entry.path();
            if !path.is_dir() {
                continue;
            }
            let name = path.file_name()?.to_string_lossy();
            if name.starts_with("NapCat.") && name.ends_with(".Shell") {
                let launcher = path.join("bootmain").join("NapCatWinBootMain.exe");
                if launcher.is_file() {
                    return Some(launcher);
                }
            }
            if let Some(launcher) = visit(&path, depth + 1) {
                return Some(launcher);
            }
        }
        None
    }

    root.is_dir().then(|| visit(root, 0)).flatten()
}

/** 读取 PID 文件并检查该进程是否仍存在；端口就绪仍是最终可用性的判断依据。 */
#[cfg(target_os = "windows")]
fn managed_process_running(root: &Path) -> bool {
    let Ok(pid) = fs::read_to_string(root.join("napcat.pid")) else {
        return false;
    };
    let pid = pid.trim();
    if pid.is_empty() || !pid.chars().all(|character| character.is_ascii_digit()) {
        return false;
    }
    Command::new("tasklist")
        .args(["/FI", &format!("PID eq {pid}"), "/FO", "CSV", "/NH"])
        .creation_flags(CREATE_NO_WINDOW)
        .output()
        .map(|output| String::from_utf8_lossy(&output.stdout).contains(pid))
        .unwrap_or(false)
}

#[cfg(not(target_os = "windows"))]
fn managed_process_running(_root: &Path) -> bool {
    false
}

/** 汇总安装目录、进程与 WebUI 状态，供连接页决定显示安装、启动还是扫码。 */
fn build_status(root: &Path, message: impl Into<String>) -> NapcatRuntimeStatus {
    let installed = find_managed_launcher(root).is_some();
    let ready = web_ui_ready();
    let running = ready || managed_process_running(root);
    let managed = installed;
    let state = if ready {
        "READY"
    } else if running {
        "STARTING"
    } else if installed {
        "INSTALLED"
    } else {
        "MISSING"
    };
    let default_message = match state {
        "READY" if managed => "NapCat 已由 Memo Echo 启动，可以获取登录二维码",
        "READY" => "检测到本机已有 NapCat，可以直接获取登录二维码",
        "STARTING" => "NapCat 正在启动，请稍候",
        "INSTALLED" => "NapCat 已安装，尚未启动",
        _ => "尚未安装托管版 NapCat",
    };
    let custom_message = message.into();
    NapcatRuntimeStatus {
        state: state.to_string(),
        installed,
        running,
        ready,
        managed,
        version: NAPCAT_VERSION.to_string(),
        web_ui_url: NAPCAT_WEB_UI_URL.to_string(),
        message: if custom_message.is_empty() {
            default_message.to_string()
        } else {
            custom_message
        },
        license_url: NAPCAT_LICENSE_URL.to_string(),
    }
}

/** 计算下载文件的 SHA-256，确保客户端执行的内容与锁定的官方发行资产完全一致。 */
fn sha256(path: &Path) -> Result<String, String> {
    let mut file = File::open(path).map_err(|error| format!("无法读取下载包：{error}"))?;
    let mut digest = Sha256::new();
    let mut buffer = [0_u8; 64 * 1024];
    loop {
        let count = file
            .read(&mut buffer)
            .map_err(|error| format!("校验下载包失败：{error}"))?;
        if count == 0 {
            break;
        }
        digest.update(&buffer[..count]);
    }
    Ok(format!("{:x}", digest.finalize()))
}

/**
 * 使用 Rust HTTP 客户端下载官方资产。
 * 客户端使用 Windows 系统根证书，能兼容系统代理注入的受信任证书。
 */
fn download_with_reqwest(partial: &Path) -> Result<(), String> {
    update_install_progress(
        "DOWNLOADING",
        0,
        NAPCAT_ARCHIVE_SIZE,
        0,
        "正在连接 NapCat 官方 GitHub Release",
        "",
    );
    let client = Client::builder()
        .connect_timeout(Duration::from_secs(12))
        // 直连只做快速尝试；网络较慢或证书链异常时尽快切换到兼容下载器。
        .timeout(Duration::from_secs(45))
        .user_agent("Memo-Echo-Desktop/0.1")
        .build()
        .map_err(|error| format!("创建下载客户端失败：{error}"))?;
    let mut response = client
        .get(NAPCAT_ARCHIVE_URL)
        .send()
        .and_then(|response| response.error_for_status())
        .map_err(|error| format!("Rust 下载失败：{error:?}"))?;
    let total_bytes = response.content_length().unwrap_or(NAPCAT_ARCHIVE_SIZE);
    let mut output = File::create(partial).map_err(|error| format!("无法保存下载包：{error}"))?;
    let started = Instant::now();
    let mut downloaded_bytes = 0_u64;
    let mut buffer = [0_u8; 64 * 1024];
    loop {
        let count = response
            .read(&mut buffer)
            .map_err(|error| format!("读取下载数据失败：{error}"))?;
        if count == 0 {
            break;
        }
        output
            .write_all(&buffer[..count])
            .map_err(|error| format!("写入下载包失败：{error}"))?;
        downloaded_bytes += count as u64;
        let elapsed = started.elapsed().as_secs_f64().max(0.001);
        update_install_progress(
            "DOWNLOADING",
            downloaded_bytes,
            total_bytes,
            (downloaded_bytes as f64 / elapsed) as u64,
            "正在从 NapCat 官方 GitHub Release 下载",
            "",
        );
    }
    output
        .sync_all()
        .map_err(|error| format!("落盘下载包失败：{error}"))?;
    if downloaded_bytes == 0 {
        return Err("官方服务器返回了空文件".to_string());
    }
    Ok(())
}

/**
 * Windows 兜底下载使用系统自带 curl，并关闭当前机器不可用的证书吊销联网检查。
 * URL 与输出路径均作为独立参数传入，不拼接用户输入；下载结果仍必须通过锁定的 SHA-256 校验。
 */
#[cfg(target_os = "windows")]
fn download_with_system_proxy(partial: &Path) -> Result<(), String> {
    let output_path = partial
        .to_str()
        .ok_or_else(|| "NapCat 下载路径包含无法传给 PowerShell 的字符".to_string())?;
    let error_log_path = partial.with_extension("download-error.log");
    let error_log =
        File::create(&error_log_path).map_err(|error| format!("无法创建系统下载日志：{error}"))?;
    let output_log = error_log
        .try_clone()
        .map_err(|error| format!("无法复制系统下载日志句柄：{error}"))?;
    let existing_size = fs::metadata(partial)
        .map(|metadata| metadata.len())
        .unwrap_or(0);
    let user_proxy = windows_user_proxy();
    update_install_progress(
        "DOWNLOADING",
        existing_size,
        NAPCAT_ARCHIVE_SIZE,
        0,
        if user_proxy.is_some() {
            "正在通过 Windows 用户代理下载，支持断点续传"
        } else {
            "正在通过 Windows 兼容下载器下载，支持断点续传"
        },
        "",
    );
    let mut arguments = vec![
        "--ssl-no-revoke".to_string(),
        "--location".to_string(),
        "--fail".to_string(),
        "--silent".to_string(),
        "--show-error".to_string(),
        "--connect-timeout".to_string(),
        "20".to_string(),
        "--max-time".to_string(),
        "1200".to_string(),
        "--retry".to_string(),
        "2".to_string(),
        "--retry-delay".to_string(),
        "2".to_string(),
        "--continue-at".to_string(),
        "-".to_string(),
    ];
    if let Some(proxy) = &user_proxy {
        arguments.extend(["--proxy".to_string(), proxy.clone()]);
    }
    arguments.extend([
        "--output".to_string(),
        output_path.to_string(),
        NAPCAT_ARCHIVE_URL.to_string(),
    ]);

    let mut child = Command::new("curl.exe")
        .args(&arguments)
        .stdin(Stdio::null())
        .stdout(Stdio::from(output_log))
        .stderr(Stdio::from(error_log))
        .creation_flags(CREATE_NO_WINDOW)
        .spawn()
        .map_err(|error| format!("无法启动 Windows curl 下载器：{error}"))?;

    let started = Instant::now();
    let mut last_size = existing_size;
    let mut last_change = Instant::now();
    let mut last_sample_size = existing_size;
    let mut last_sample_at = Instant::now();
    let mut displayed_speed = 0_u64;
    loop {
        if let Some(status) = child
            .try_wait()
            .map_err(|error| format!("读取 Windows 兼容下载器状态失败：{error}"))?
        {
            if status.success() {
                let final_size = fs::metadata(partial)
                    .map(|metadata| metadata.len())
                    .unwrap_or(0);
                update_install_progress(
                    "DOWNLOADING",
                    final_size,
                    NAPCAT_ARCHIVE_SIZE,
                    0,
                    "下载完成，准备校验安装包",
                    "",
                );
                let _ = fs::remove_file(&error_log_path);
                return (final_size > 0)
                    .then_some(())
                    .ok_or_else(|| "Windows 兼容下载器返回了空文件".to_string());
            }
            let message = fs::read_to_string(&error_log_path).unwrap_or_default();
            return Err(if message.trim().is_empty() {
                format!("Windows 兼容下载器退出码：{status}")
            } else {
                format!("Windows 兼容下载失败：{}", message.trim())
            });
        }

        let downloaded_bytes = fs::metadata(partial)
            .map(|metadata| metadata.len())
            .unwrap_or(0);
        if downloaded_bytes != last_size {
            last_size = downloaded_bytes;
            last_change = Instant::now();
        }
        let sample_elapsed = last_sample_at.elapsed();
        if sample_elapsed >= Duration::from_secs(1) {
            let sample_speed = downloaded_bytes
                .saturating_sub(last_sample_size)
                .checked_div(sample_elapsed.as_secs().max(1))
                .unwrap_or(0);
            displayed_speed = if displayed_speed == 0 {
                sample_speed
            } else {
                (displayed_speed * 3 + sample_speed * 7) / 10
            };
            last_sample_size = downloaded_bytes;
            last_sample_at = Instant::now();
        }
        update_install_progress(
            "DOWNLOADING",
            downloaded_bytes,
            NAPCAT_ARCHIVE_SIZE,
            displayed_speed,
            if user_proxy.is_some() {
                "正在通过 Windows 用户代理下载 NapCat 官方安装包"
            } else {
                "正在通过 Windows 兼容下载器下载 NapCat 官方安装包"
            },
            "",
        );
        if started.elapsed() > Duration::from_secs(20 * 60)
            || last_change.elapsed() > Duration::from_secs(90)
        {
            let _ = child.kill();
            let _ = child.wait();
            return Err("Windows 兼容下载器长时间没有收到数据，请检查网络或代理后重试".to_string());
        }
        thread::sleep(Duration::from_millis(350));
    }
}

#[cfg(not(target_os = "windows"))]
fn download_with_system_proxy(_partial: &Path) -> Result<(), String> {
    Err("当前平台不支持 Windows 系统下载兜底".to_string())
}

/** 从 NapCat 官方 GitHub Release 下载锁定版本；先写临时文件，校验通过后再替换正式包。 */
fn download_archive(archive: &Path) -> Result<(), String> {
    if archive.is_file() {
        update_install_progress(
            "VERIFYING",
            NAPCAT_ARCHIVE_SIZE,
            NAPCAT_ARCHIVE_SIZE,
            0,
            "正在校验本地安装包",
            "",
        );
        if sha256(archive)? == NAPCAT_ARCHIVE_SHA256 {
            return Ok(());
        }
        fs::remove_file(archive).map_err(|error| format!("清理无效安装包失败：{error}"))?;
    }
    let partial = archive.with_extension("zip.partial");
    if fs::metadata(&partial)
        .map(|metadata| metadata.len() > NAPCAT_ARCHIVE_SIZE)
        .unwrap_or(false)
    {
        let _ = fs::remove_file(&partial);
    }

    #[cfg(target_os = "windows")]
    if let Err(primary_error) = download_with_system_proxy(&partial) {
        let _ = fs::remove_file(&partial);
        if let Err(fallback_error) = download_with_reqwest(&partial) {
            let _ = fs::remove_file(&partial);
            return Err(format!(
                "下载 NapCat 官方安装包失败。{primary_error}；{fallback_error}"
            ));
        }
    }

    #[cfg(not(target_os = "windows"))]
    download_with_reqwest(&partial)?;

    update_install_progress(
        "VERIFYING",
        NAPCAT_ARCHIVE_SIZE,
        NAPCAT_ARCHIVE_SIZE,
        0,
        "下载完成，正在校验 SHA-256",
        "",
    );
    let actual_hash = sha256(&partial)?;
    if actual_hash != NAPCAT_ARCHIVE_SHA256 {
        let _ = fs::remove_file(&partial);
        return Err(format!(
            "NapCat 下载包校验失败，期望 {NAPCAT_ARCHIVE_SHA256}，实际 {actual_hash}"
        ));
    }
    fs::rename(&partial, archive).map_err(|error| format!("保存已校验安装包失败：{error}"))
}

/** 安全解压 ZIP：拒绝绝对路径、目录穿越和符号链接，防止发行包内容逃逸运行目录。 */
fn extract_archive(archive: &Path, destination: &Path) -> Result<(), String> {
    let file = File::open(archive).map_err(|error| format!("无法打开 NapCat 安装包：{error}"))?;
    let mut zip = zip::ZipArchive::new(file)
        .map_err(|error| format!("NapCat 安装包不是有效 ZIP：{error}"))?;
    for index in 0..zip.len() {
        let mut entry = zip
            .by_index(index)
            .map_err(|error| format!("读取 NapCat 安装包条目失败：{error}"))?;
        let Some(relative) = entry.enclosed_name() else {
            return Err(format!("NapCat 安装包包含不安全路径：{}", entry.name()));
        };
        if entry
            .unix_mode()
            .is_some_and(|mode| mode & 0o170000 == 0o120000)
        {
            return Err(format!(
                "NapCat 安装包包含不允许的符号链接：{}",
                entry.name()
            ));
        }
        let output = destination.join(relative);
        if entry.is_dir() {
            fs::create_dir_all(&output).map_err(|error| format!("创建安装目录失败：{error}"))?;
            continue;
        }
        if let Some(parent) = output.parent() {
            fs::create_dir_all(parent).map_err(|error| format!("创建安装目录失败：{error}"))?;
        }
        let mut target =
            File::create(&output).map_err(|error| format!("创建安装文件失败：{error}"))?;
        io::copy(&mut entry, &mut target)
            .map_err(|error| format!("解压 NapCat 文件失败：{error}"))?;
    }
    Ok(())
}

/** 只保留运行日志尾部，既便于排错，也避免把过长的第三方输出直接塞进客户端界面。 */
fn read_log_tail(path: &Path, max_chars: usize) -> String {
    fs::read(path)
        .ok()
        .map(|bytes| String::from_utf8_lossy(&bytes).into_owned())
        .map(|text| {
            text.chars()
                .rev()
                .take(max_chars)
                .collect::<String>()
                .chars()
                .rev()
                .collect()
        })
        .unwrap_or_default()
}

/** 完成官方 Node 运行时的下载、哈希校验和安全解压，不会把 NapCat 二进制写入 Memo Echo 仓库。 */
fn install_runtime(root: &Path) -> Result<NapcatRuntimeStatus, String> {
    if cfg!(not(all(target_os = "windows", target_arch = "x86_64"))) {
        return Err("NapCat 托管运行时目前仅支持 Windows AMD64".to_string());
    }
    if find_managed_launcher(root).is_some() {
        update_install_progress(
            "COMPLETED",
            NAPCAT_ARCHIVE_SIZE,
            NAPCAT_ARCHIVE_SIZE,
            0,
            "NapCat 已安装",
            "",
        );
        return Ok(build_status(root, "NapCat 已安装"));
    }
    fs::create_dir_all(root).map_err(|error| format!("创建 NapCat 运行目录失败：{error}"))?;
    let archive = root.join(format!("NapCat.Shell.Windows.Node-{NAPCAT_VERSION}.zip"));
    download_archive(&archive)?;
    let version_dir = root.join(NAPCAT_VERSION);
    if version_dir.exists() {
        fs::remove_dir_all(&version_dir).map_err(|error| format!("清理未完成安装失败：{error}"))?;
    }
    fs::create_dir_all(&version_dir).map_err(|error| format!("创建版本目录失败：{error}"))?;
    update_install_progress(
        "EXTRACTING",
        NAPCAT_ARCHIVE_SIZE,
        NAPCAT_ARCHIVE_SIZE,
        0,
        "校验通过，正在安全解压 NapCat",
        "",
    );
    extract_archive(&archive, &version_dir)?;
    if find_managed_launcher(&version_dir).is_none() {
        return Err("NapCat 官方 Node 资产中未找到完整启动文件".to_string());
    }
    fs::write(root.join("installed-version"), NAPCAT_VERSION)
        .map_err(|error| format!("写入安装版本标记失败：{error}"))?;
    update_install_progress(
        "COMPLETED",
        NAPCAT_ARCHIVE_SIZE,
        NAPCAT_ARCHIVE_SIZE,
        0,
        "NapCat 安装完成",
        "",
    );
    Ok(build_status(root, "NapCat 官方运行时已安装，可以启动"))
}

/** 隐藏启动托管运行时并等待 WebUI 就绪；超时会保留日志，便于用户稍后重试。 */
#[cfg(target_os = "windows")]
fn start_runtime(
    root: &Path,
    requested_account_id: Option<String>,
) -> Result<NapcatRuntimeStatus, String> {
    // 前端传入的当前连接账号优先；未传入时复用首次扫码成功后保存的账号。
    let account_id =
        normalize_account_id(requested_account_id.as_deref()).or_else(|| read_last_account(root));
    if let Some(account_id) = account_id.as_deref() {
        write_last_account(root, account_id)?;
    }
    if web_ui_ready() {
        return Ok(build_status(root, "NapCat 已在运行"));
    }
    let launcher = find_managed_launcher(root).ok_or_else(|| "NapCat 尚未安装".to_string())?;
    let working_directory = launcher
        .parent()
        .ok_or_else(|| "NapCat 启动目录无效".to_string())?;
    let log_path = root.join("runtime.log");
    let log = OpenOptions::new()
        .create(true)
        .append(true)
        .open(&log_path)
        .map_err(|error| format!("无法创建 NapCat 运行日志：{error}"))?;
    let error_log = log
        .try_clone()
        .map_err(|error| format!("无法创建运行日志副本：{error}"))?;
    let is_node_runtime = launcher
        .file_name()
        .is_some_and(|name| name.eq_ignore_ascii_case("node.exe"));
    let mut command = Command::new(&launcher);
    command.current_dir(working_directory);
    if is_node_runtime {
        command.arg("index.js");
        if let Some(account_id) = account_id.as_deref() {
            // Node 运行包通过 -q 指定已有账号，NapCat 会复用 QQ 保存的登录凭据。
            command.args(["-q", account_id]);
        }
    } else if let Some(account_id) = account_id.as_deref() {
        // 旧 Shell 启动器使用位置参数接收快速登录账号。
        command.arg(account_id);
    }
    let child = command
        .stdin(Stdio::null())
        .stdout(Stdio::from(log))
        .stderr(Stdio::from(error_log))
        .creation_flags(CREATE_NO_WINDOW)
        .spawn()
        .map_err(|error| format!("无法启动 NapCat：{error}"))?;
    fs::write(root.join("napcat.pid"), child.id().to_string())
        .map_err(|error| format!("记录 NapCat 进程失败：{error}"))?;
    let started = Instant::now();
    while started.elapsed() < START_TIMEOUT {
        if web_ui_ready() {
            let message = account_id
                .as_deref()
                .map(|account_id| format!("NapCat 已启动，正在恢复 QQ {account_id} 的登录状态"))
                .unwrap_or_else(|| "NapCat 已启动，可以扫码登录".to_string());
            return Ok(build_status(root, message));
        }
        thread::sleep(Duration::from_millis(700));
    }
    Err(format!(
        "NapCat 已启动但 WebUI 在 120 秒内未就绪。{}",
        read_log_tail(&log_path, 500)
    ))
}

#[cfg(not(target_os = "windows"))]
fn start_runtime(
    _root: &Path,
    _requested_account_id: Option<String>,
) -> Result<NapcatRuntimeStatus, String> {
    Err("当前托管启动仅支持 Windows AMD64".to_string())
}

/** 查询托管运行时状态；该命令不会下载、启动或修改任何文件。 */
#[tauri::command]
pub fn get_napcat_runtime_status(app: AppHandle) -> Result<NapcatRuntimeStatus, String> {
    let root = runtime_root(&app)?;
    Ok(build_status(&root, ""))
}

/** 查询后台安装任务快照；该命令只读取内存，不接触网络和磁盘。 */
#[tauri::command]
pub fn get_napcat_runtime_install_progress() -> NapcatInstallProgress {
    install_progress_snapshot()
}

/**
 * 启动非阻塞安装任务。
 * 重复点击会复用正在执行的任务，前端通过进度查询命令轮询，不会卡住 Tauri IPC。
 */
#[tauri::command]
pub fn start_napcat_runtime_install(app: AppHandle) -> Result<NapcatInstallProgress, String> {
    let root = runtime_root(&app)?;
    let current = install_progress_snapshot();
    if matches!(
        current.state.as_str(),
        "DOWNLOADING" | "VERIFYING" | "EXTRACTING"
    ) {
        return Ok(current);
    }
    if find_managed_launcher(&root).is_some() {
        update_install_progress(
            "COMPLETED",
            NAPCAT_ARCHIVE_SIZE,
            NAPCAT_ARCHIVE_SIZE,
            0,
            "NapCat 已安装",
            "",
        );
        return Ok(install_progress_snapshot());
    }

    update_install_progress(
        "DOWNLOADING",
        0,
        NAPCAT_ARCHIVE_SIZE,
        0,
        "正在准备下载 NapCat 官方运行时",
        "",
    );
    tauri::async_runtime::spawn_blocking(move || {
        if let Err(error) = install_runtime(&root) {
            let snapshot = install_progress_snapshot();
            update_install_progress(
                "FAILED",
                snapshot.downloaded_bytes,
                snapshot.total_bytes,
                0,
                "NapCat 安装失败",
                &error,
            );
        }
    });
    Ok(install_progress_snapshot())
}

/** 用户确认许可后安装官方运行时；耗时工作放在线程池，避免阻塞 Tauri 窗口事件循环。 */
#[tauri::command]
pub async fn install_napcat_runtime(app: AppHandle) -> Result<NapcatRuntimeStatus, String> {
    let root = runtime_root(&app)?;
    tauri::async_runtime::spawn_blocking(move || install_runtime(&root))
        .await
        .map_err(|error| format!("NapCat 安装任务异常结束：{error}"))?
}

/** 启动已安装的运行时；传入最近账号时优先尝试恢复现有 QQ 会话。 */
#[tauri::command]
pub async fn start_napcat_runtime(
    app: AppHandle,
    account_id: Option<String>,
) -> Result<NapcatRuntimeStatus, String> {
    let root = runtime_root(&app)?;
    tauri::async_runtime::spawn_blocking(move || start_runtime(&root, account_id))
        .await
        .map_err(|error| format!("NapCat 启动任务异常结束：{error}"))?
}

/** 首次扫码成功后记录账号；这里只保存 QQ 号，不保存密码、扫码凭证或登录 Token。 */
#[tauri::command]
pub fn remember_napcat_account(app: AppHandle, account_id: String) -> Result<(), String> {
    let root = runtime_root(&app)?;
    write_last_account(&root, &account_id).map(|_| ())
}

/**
 * 停止 Memo Echo 记录的启动器进程。
 * 若当前使用的是用户自行启动的外部 NapCat（managed=false），本命令不会终止它。
 */
#[tauri::command]
#[cfg(target_os = "windows")]
pub fn stop_napcat_runtime(app: AppHandle) -> Result<NapcatRuntimeStatus, String> {
    let root = runtime_root(&app)?;
    let pid = fs::read_to_string(root.join("napcat.pid"))
        .map_err(|_| "没有可停止的托管进程".to_string())?;
    let pid = pid.trim();
    if pid.is_empty() || !pid.chars().all(|character| character.is_ascii_digit()) {
        return Err("NapCat PID 记录无效".to_string());
    }
    let status = Command::new("taskkill")
        .args(["/PID", pid, "/T", "/F"])
        .creation_flags(CREATE_NO_WINDOW)
        .status()
        .map_err(|error| format!("停止 NapCat 失败：{error}"))?;
    if !status.success() {
        return Err("NapCat 进程未能停止，可能已经退出".to_string());
    }
    let _ = fs::remove_file(root.join("napcat.pid"));
    Ok(build_status(&root, "NapCat 已停止"))
}

#[tauri::command]
#[cfg(not(target_os = "windows"))]
pub fn stop_napcat_runtime(app: AppHandle) -> Result<NapcatRuntimeStatus, String> {
    let root = runtime_root(&app)?;
    Err(format!("当前平台不支持托管停止：{}", root.display()))
}

#[cfg(test)]
mod tests {
    use super::{
        find_managed_launcher, normalize_account_id, read_last_account, write_last_account,
    };
    use std::fs;
    use std::path::PathBuf;
    use std::time::{SystemTime, UNIX_EPOCH};

    /** 为单元测试创建唯一临时目录，测试结束后可安全地整体删除。 */
    fn test_directory(name: &str) -> PathBuf {
        let nonce = SystemTime::now()
            .duration_since(UNIX_EPOCH)
            .expect("system clock must be after Unix epoch")
            .as_nanos();
        std::env::temp_dir().join(format!(
            "memo-echo-napcat-{name}-{}-{nonce}",
            std::process::id()
        ))
    }

    /** 快速登录账号必须是合理长度的纯数字 QQ 号。 */
    #[test]
    fn validates_quick_login_account_id() {
        assert_eq!(
            normalize_account_id(Some(" 3969785168 ")),
            Some("3969785168".to_string())
        );
        assert_eq!(normalize_account_id(Some("qq-3969785168")), None);
        assert_eq!(normalize_account_id(Some("1234")), None);
    }

    /** 最近登录账号写入后必须能跨进程从固定文件恢复。 */
    #[test]
    fn persists_last_login_account() {
        let root = test_directory("last-account");
        write_last_account(&root, "3969785168").expect("account should be persisted");
        assert_eq!(read_last_account(&root), Some("3969785168".to_string()));
        fs::remove_dir_all(root).expect("temporary directory should be removed");
    }

    /** 完整 Node 资产必须同时包含 node.exe、index.js 和 NapCat 启动器。 */
    #[test]
    fn recognizes_complete_node_runtime_layout() {
        let root = test_directory("complete");
        let version = root.join("v4.18.9");
        fs::create_dir_all(version.join("napcat")).expect("create fake runtime directory");
        fs::write(version.join("node.exe"), b"node").expect("write fake node executable");
        fs::write(version.join("index.js"), b"entry").expect("write fake entry script");
        fs::write(version.join("napcat/NapCatWinBootMain.exe"), b"boot")
            .expect("write fake NapCat launcher");

        assert_eq!(find_managed_launcher(&root), Some(version.join("node.exe")));
        fs::remove_dir_all(root).expect("remove fake runtime directory");
    }

    /** 缺少入口脚本的残缺下载不能被视为已安装，避免启动阶段才暴露问题。 */
    #[test]
    fn rejects_incomplete_node_runtime_layout() {
        let root = test_directory("incomplete");
        let version = root.join("v4.18.9");
        fs::create_dir_all(version.join("napcat")).expect("create fake runtime directory");
        fs::write(version.join("node.exe"), b"node").expect("write fake node executable");
        fs::write(version.join("napcat/NapCatWinBootMain.exe"), b"boot")
            .expect("write fake NapCat launcher");

        assert_eq!(find_managed_launcher(&root), None);
        fs::remove_dir_all(root).expect("remove fake runtime directory");
    }
}
