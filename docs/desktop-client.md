# 桌面客户端

客户端位于 `desktop-client/`，采用 Tauri 2 + React + TypeScript。它以 Rust 提供原生壳层和系统凭据管理能力，React 只负责渲染工作台界面。

第一阶段功能：

- 连接本地 `event-center-service`
- 注册、登录与退出
- 使用 Windows 凭据管理器保存 token
- 展示当前用户的平台连接和模型配置概览

客户端只使用用户 JWT 调用配置接口，不读取 API Key、NapCat Token 或 runtime 服务令牌。

## 本地 CORS

`event-center-service` 默认只允许 Tauri 与本地 Vite 开发地址跨域访问。若客户端使用了其他来源，可通过环境变量覆盖白名单：

```powershell
$env:EVENT_CENTER_ALLOWED_ORIGINS = "http://127.0.0.1:5173,http://tauri.localhost,tauri://localhost"
```
