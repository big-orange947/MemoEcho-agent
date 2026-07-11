# 本地账户与 JWT 认证

event-center 提供本地账户注册和登录，用于把平台连接、会话设定和模型配置逐步绑定到真实用户。密码使用随机盐 PBKDF2-SHA256 哈希保存，JWT 使用 HMAC-SHA256 签名。

## 注册

```http
POST /api/auth/register
Content-Type: application/json
```

```json
{
  "username": "freeze",
  "password": "至少八位密码",
  "displayName": "Freeze"
}
```

用户名只能包含字母、数字、下划线、点和短横线，长度为 `3-64`。注册成功返回 `201` 和登录令牌。

## 登录

```http
POST /api/auth/login
Content-Type: application/json
```

```json
{
  "username": "freeze",
  "password": "至少八位密码"
}
```

响应示例：

```json
{
  "tokenType": "Bearer",
  "accessToken": "eyJ...",
  "expiresIn": 604800,
  "userId": "b51a...",
  "username": "freeze",
  "displayName": "Freeze"
}
```

调用连接管理接口时传入：

```http
Authorization: Bearer eyJ...
```

Bearer Token 的用户身份优先级高于 `X-Memo-Echo-User-Id`。Token 无效、被篡改、过期，或者对应用户已停用时返回 `401`。

## 部署配置

```text
EVENT_CENTER_JWT_SECRET=请替换为足够长的随机密钥
EVENT_CENTER_JWT_EXPIRES_SECONDS=604800
EVENT_CENTER_ALLOW_LEGACY_USER_HEADER=false
```

默认已关闭旧用户头兼容模式。仅在迁移旧版本地联调脚本时，才临时设置 `EVENT_CENTER_ALLOW_LEGACY_USER_HEADER=true`；启用后调用方可以伪造开发期用户头，因此不得用于公开部署。
