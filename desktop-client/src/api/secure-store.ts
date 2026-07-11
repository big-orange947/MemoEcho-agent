import { invoke } from "@tauri-apps/api/core";
import type { StoredCredential } from "../types";

/** 从 Windows 凭据管理器读取并解析桌面端保存的登录状态。 */
export async function loadCredential(): Promise<StoredCredential | null> {
  const raw = await invoke<string | null>("read_credential");
  return raw ? JSON.parse(raw) as StoredCredential : null;
}

/** 将登录状态写入 Windows 凭据管理器，令牌不会进入 localStorage。 */
export function saveCredential(credential: StoredCredential) {
  return invoke("write_credential", { value: JSON.stringify(credential) });
}

/** 清除 Windows 凭据管理器中的登录状态。 */
export function removeCredential() {
  return invoke("clear_credential");
}
