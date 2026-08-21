import { invoke } from "@tauri-apps/api/core";
import type { StoredCredential } from "../types";

/** 判断是否运行在 Tauri 原生壳内；纯浏览器 vite dev 预览不会具备 Tauri IPC。 */
function isTauriRuntime(): boolean {
  return typeof window !== "undefined" && "__TAURI_INTERNALS__" in window;
}

/**
 * 仅限非 Tauri 环境的开发预览降级存储。
 * Tauri 生产构建仍走 Windows 凭据管理器，令牌不会进入 localStorage。
 */
const BROWSER_DEV_KEY = "memo-echo-dev-credential";

function readBrowserFallback(): StoredCredential | null {
  try {
    const raw = window.localStorage.getItem(BROWSER_DEV_KEY);
    return raw ? (JSON.parse(raw) as StoredCredential) : null;
  } catch {
    return null;
  }
}

function writeBrowserFallback(credential: StoredCredential | null) {
  try {
    if (credential === null) {
      window.localStorage.removeItem(BROWSER_DEV_KEY);
    } else {
      window.localStorage.setItem(BROWSER_DEV_KEY, JSON.stringify(credential));
    }
  } catch {
    // 预览环境存储不可用时静默降级为"每次都要登录"。
  }
}

/** 从 Windows 凭据管理器读取并解析桌面端保存的登录状态。 */
export async function loadCredential(): Promise<StoredCredential | null> {
  if (!isTauriRuntime()) {
    return readBrowserFallback();
  }
  const raw = await invoke<string | null>("read_credential");
  return raw ? JSON.parse(raw) as StoredCredential : null;
}

/** 将登录状态写入 Windows 凭据管理器，令牌不会进入 localStorage（Tauri 构建）。 */
export function saveCredential(credential: StoredCredential) {
  if (!isTauriRuntime()) {
    writeBrowserFallback(credential);
    return Promise.resolve();
  }
  return invoke("write_credential", { value: JSON.stringify(credential) });
}

/** 清除 Windows 凭据管理器中的登录状态。 */
export function removeCredential() {
  if (!isTauriRuntime()) {
    writeBrowserFallback(null);
    return Promise.resolve();
  }
  return invoke("clear_credential");
}