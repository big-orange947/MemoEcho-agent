use keyring::Entry;

const SERVICE_NAME: &str = "memo-echo-desktop";
const ACCOUNT_NAME: &str = "session";

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
    credential_entry()?.set_password(&value).map_err(|error| error.to_string())
}

/** 从 Windows 凭据管理器删除登录信息，实现本地安全退出。 */
#[tauri::command]
fn clear_credential() -> Result<(), String> {
    match credential_entry()?.delete_credential() {
        Ok(()) | Err(keyring::Error::NoEntry) => Ok(()),
        Err(error) => Err(error.to_string()),
    }
}

/** 组装 Tauri 应用并注册仅用于凭据读写的最小 IPC 命令面。 */
pub fn run() {
    tauri::Builder::default()
        .invoke_handler(tauri::generate_handler![read_credential, write_credential, clear_credential])
        .run(tauri::generate_context!())
        .expect("failed to start Memo Echo desktop client");
}
