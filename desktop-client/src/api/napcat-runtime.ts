import { invoke } from "@tauri-apps/api/core";

/** Tauri 原生层返回的 NapCat 托管运行状态。 */
export type NapcatRuntimeStatus = {
  state: "MISSING" | "INSTALLED" | "STARTING" | "READY";
  installed: boolean;
  running: boolean;
  ready: boolean;
  managed: boolean;
  version: string;
  webUiUrl: string;
  message: string;
  licenseUrl: string;
};

/** NapCat 后台安装任务的实时进度，下载完成后还会经历校验和解压阶段。 */
export type NapcatInstallProgress = {
  state: "IDLE" | "DOWNLOADING" | "VERIFYING" | "EXTRACTING" | "COMPLETED" | "FAILED";
  downloadedBytes: number;
  totalBytes: number;
  percent: number;
  bytesPerSecond: number;
  message: string;
  error: string;
};

/** 只读取本机状态，不会下载或启动 NapCat。 */
export function getNapcatRuntimeStatus() {
  return invoke<NapcatRuntimeStatus>("get_napcat_runtime_status");
}

/** 下载并安装已锁定且通过 SHA-256 校验的 NapCat 官方 Windows Node 运行包。 */
export function installNapcatRuntime() {
  return invoke<NapcatRuntimeStatus>("install_napcat_runtime");
}

/** 启动非阻塞安装任务；若任务已在执行，原生层会返回同一任务的当前进度。 */
export function startNapcatRuntimeInstall() {
  return invoke<NapcatInstallProgress>("start_napcat_runtime_install");
}

/** 查询安装进度快照，不会重复触发下载。 */
export function getNapcatRuntimeInstallProgress() {
  return invoke<NapcatInstallProgress>("get_napcat_runtime_install_progress");
}

/** 隐藏启动托管运行时；传入最近账号时会优先复用 QQ 已保存的本地会话。 */
export function startNapcatRuntime(accountId?: string) {
  return invoke<NapcatRuntimeStatus>("start_napcat_runtime", { accountId: accountId || null });
}

/** 首次扫码成功后只记录 QQ 号，供后续启动选择快速登录账号。 */
export function rememberNapcatAccount(accountId: string) {
  return invoke<void>("remember_napcat_account", { accountId });
}

/** 停止由 Memo Echo 启动并记录 PID 的运行时，不会终止用户自行启动的实例。 */
export function stopNapcatRuntime() {
  return invoke<NapcatRuntimeStatus>("stop_napcat_runtime");
}
