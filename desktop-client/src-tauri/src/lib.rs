use keyring::Entry;
use serde::Serialize;
use std::fs;
use std::path::{Path, PathBuf};
use std::time::UNIX_EPOCH;

mod napcat_runtime;

const SERVICE_NAME: &str = "memo-echo-desktop";
const ACCOUNT_NAME: &str = "session";
const QCE_SCAN_MAX_DEPTH: usize = 4;
const QCE_SCAN_MAX_FILES: usize = 200;
const QCE_EXPORT_MAX_BYTES: u64 = 128 * 1024 * 1024;

/** 桌面端提供给前端的 QCE JSON 文件元数据；不包含聊天正文。 */
#[derive(Serialize)]
#[serde(rename_all = "camelCase")]
struct QceExportFile {
    path: String,
    name: String,
    modified_at: u64,
    size: u64,
}

/** 创建连接到操作系统凭据管理器的 Memo Echo 登录令牌条目。 */
fn credential_entry() -> Result<Entry, String> {
    Entry::new(SERVICE_NAME, ACCOUNT_NAME).map_err(|error| error.to_string())
}

/** 从 Windows 凭据管理器读取已保存的登录信息，不存在时返回空值。 */
#[tauri::command]
fn read_credential() -> Result<Option<String>, String> {
    match credential_entry()?.get_password() {
        Ok(value) => Ok(Some(value)),
        Err(keyring::Error::NoEntry) => Ok(None),
        Err(error) => Err(error.to_string()),
    }
}

/** 将登录信息写入 Windows 凭据管理器，避免 token 落到项目目录或 localStorage。 */
#[tauri::command]
fn write_credential(value: String) -> Result<(), String> {
    credential_entry()?
        .set_password(&value)
        .map_err(|error| error.to_string())
}

/** 从 Windows 凭据管理器删除登录信息，实现本地安全退出。 */
#[tauri::command]
fn clear_credential() -> Result<(), String> {
    match credential_entry()?.delete_credential() {
        Ok(()) | Err(keyring::Error::NoEntry) => Ok(()),
        Err(error) => Err(error.to_string()),
    }
}

/** 打开原生文件夹选择器，让用户明确授权 QCE 导出目录。 */
#[tauri::command]
fn pick_qce_export_directory() -> Option<String> {
    rfd::FileDialog::new()
        .set_title("选择 QQ Chat Exporter 的 JSON 导出目录")
        .pick_folder()
        .map(|path| path.to_string_lossy().to_string())
}

/**
 * 扫描用户已授权目录中的 QCE JSON 文件。
 * 只返回文件名、路径和修改时间，聊天正文仍在用户明确触发自动导入时才读取。
 */
#[tauri::command]
fn list_qce_export_files(directory: String) -> Result<Vec<QceExportFile>, String> {
    let root = PathBuf::from(directory);
    if !root.is_dir() {
        return Err("所选目录不存在或不可访问".to_string());
    }

    let mut files = Vec::new();
    collect_qce_json_files(&root, 0, &mut files)?;
    files.sort_by_key(|file| file.modified_at);
    Ok(files)
}

/** 读取单个 QCE JSON 文件；限制容量以避免误读超大无关文件。 */
#[tauri::command]
fn read_qce_export_file(path: String) -> Result<String, String> {
    let file_path = PathBuf::from(path);
    let metadata = fs::metadata(&file_path).map_err(|error| error.to_string())?;
    if metadata.len() > QCE_EXPORT_MAX_BYTES {
        return Err("QCE JSON 超过 128 MB，请使用 QCE 的分段导出或手动筛选时间范围".to_string());
    }
    fs::read_to_string(file_path).map_err(|error| error.to_string())
}

/** 递归收集有限层级内的 JSON，避免扫描导出目录之外的文件。 */
fn collect_qce_json_files(
    directory: &Path,
    depth: usize,
    files: &mut Vec<QceExportFile>,
) -> Result<(), String> {
    if depth > QCE_SCAN_MAX_DEPTH || files.len() >= QCE_SCAN_MAX_FILES {
        return Ok(());
    }
    for entry in fs::read_dir(directory).map_err(|error| error.to_string())? {
        if files.len() >= QCE_SCAN_MAX_FILES {
            break;
        }
        let entry = entry.map_err(|error| error.to_string())?;
        let path = entry.path();
        let metadata = entry.metadata().map_err(|error| error.to_string())?;
        if metadata.is_dir() {
            collect_qce_json_files(&path, depth + 1, files)?;
            continue;
        }
        let is_json = path
            .extension()
            .and_then(|extension| extension.to_str())
            .is_some_and(|extension| extension.eq_ignore_ascii_case("json"));
        if !is_json || metadata.len() > QCE_EXPORT_MAX_BYTES {
            continue;
        }
        let modified_at = metadata
            .modified()
            .ok()
            .and_then(|time| time.duration_since(UNIX_EPOCH).ok())
            .map(|duration| duration.as_secs())
            .unwrap_or_default();
        files.push(QceExportFile {
            path: path.to_string_lossy().to_string(),
            name: path
                .file_name()
                .and_then(|name| name.to_str())
                .unwrap_or("QCE export.json")
                .to_string(),
            modified_at,
            size: metadata.len(),
        });
    }
    Ok(())
}

/** 组装 Tauri 应用并注册仅用于凭据读写的最小 IPC 命令面。 */
pub fn run() {
    tauri::Builder::default()
        .invoke_handler(tauri::generate_handler![
            read_credential,
            write_credential,
            clear_credential,
            pick_qce_export_directory,
            list_qce_export_files,
            read_qce_export_file,
            napcat_runtime::get_napcat_runtime_status,
            napcat_runtime::get_napcat_runtime_install_progress,
            napcat_runtime::start_napcat_runtime_install,
            napcat_runtime::install_napcat_runtime,
            napcat_runtime::start_napcat_runtime,
            napcat_runtime::remember_napcat_account,
            napcat_runtime::stop_napcat_runtime
        ])
        .run(tauri::generate_context!())
        .expect("failed to start Memo Echo desktop client");
}
