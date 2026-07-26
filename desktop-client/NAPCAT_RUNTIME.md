# NapCat 托管运行时

Memo Echo 桌面端不会把 NapCat 二进制提交到仓库或打进安装包。用户首次点击“扫码连接 QQ”并确认第三方许可后，Tauri 原生层执行以下流程：

1. 从 NapCatQQ 官方 GitHub Release 下载锁定版本的 Windows Node 运行时。该资产也是 OneKey 引导器最终使用的完整无头运行包。
2. 使用内置的 SHA-256 摘要校验下载内容。
3. 安全解压到应用本地数据目录，拒绝目录穿越和符号链接。
4. 隐藏执行官方运行时的 `node.exe index.js`。
5. 等待本机 `127.0.0.1:6099` WebUI 就绪，再交给现有二维码登录链路。

## 当前锁定版本

- 版本：`v4.18.9`
- 资产：`NapCat.Shell.Windows.Node.zip`
- SHA-256：`234f2b9341d355d107881ce486d6699f529300d644282e25af452717d00a50da`
- 下载源：`https://github.com/NapNeko/NapCatQQ/releases/download/v4.18.9/NapCat.Shell.Windows.Node.zip`

升级版本时必须同时更新 Rust 模块中的版本、下载地址和 SHA-256，并重新执行 Rust 与桌面端构建检查。不要改成不锁版本的 `latest` 下载地址。

## 边界

- NapCat 是独立第三方项目，不是 Memo Echo 的组成部分。
- 首次安装前必须由用户主动确认 NapCat 许可。
- 若已检测到用户自行启动的 6099 WebUI，Memo Echo 只复用该实例，不会停止或修改它。
- 托管停止命令只处理 Memo Echo 自己记录的启动器 PID。
