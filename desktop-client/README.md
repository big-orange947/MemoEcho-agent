# Memo Echo Desktop

基于 Tauri、React 和 TypeScript 的本地优先桌面客户端。

## 启动

```powershell
npm install
npm run dev
```

启动前先运行 `event-center-service`，默认地址为 `http://127.0.0.1:8093`。

当前已实现：本地注册/登录、Electron 安全存储登录令牌、连接状态与模型配置概览。

登录令牌通过 Rust `keyring` 写入 Windows 凭据管理器，不会保存在项目目录或浏览器 localStorage。
