// 隐藏 Windows GUI 程序启动时多余的终端窗口。
#![cfg_attr(not(debug_assertions), windows_subsystem = "windows")]

/** 启动 Tauri 桌面应用。 */
fn main() {
    memo_echo_desktop_lib::run();
}
