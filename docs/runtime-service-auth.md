# Python Runtime 服务认证

Python runtime 会代表当前桌面用户向 event-center 解析模型配置。该调用不能只依赖请求体中的 `userId`，因此使用独立服务令牌认证。

## 配置

为 Java event-center 和 Python runtime 设置相同的随机令牌：

```powershell
$env:EVENT_CENTER_RUNTIME_TOKEN = "替换为至少32字节的随机值"
$env:MEMO_ECHO_RUNTIME_USER_ID = "登录或注册接口返回的 userId"
```

`MEMO_ECHO_RUNTIME_USER_ID` 必须使用 `/api/auth/register` 或 `/api/auth/login` 响应中的 `userId`，不是用户名或 QQ 号。

Python runtime 在调用 `POST /internal/user-model-profiles/resolve` 时自动发送：

```http
X-Memo-Echo-Runtime-Token: <EVENT_CENTER_RUNTIME_TOKEN>
X-Memo-Echo-User-Id: <MEMO_ECHO_RUNTIME_USER_ID>
```

event-center 会校验服务令牌，并确认目标用户存在且处于启用状态。

## 严格模式

默认已关闭旧用户头兼容；只有为了迁移已有本地配置时，才应临时设置：

```powershell
$env:EVENT_CENTER_ALLOW_LEGACY_USER_HEADER = "true"
```

正常的严格模式下：

- 工作台接口必须使用用户登录 JWT。
- Python runtime 必须使用 runtime 服务令牌。
- 未认证的请求无法通过请求体 `userId` 读取或解析模型配置。

不要把 `EVENT_CENTER_RUNTIME_TOKEN` 提交到 Git，也不要将 event-center 的内部端口直接暴露给公网。
