# 模型配置的用户隔离

`/internal/user-model-profiles` 的 CRUD 接口已使用本地登录身份进行用户隔离。

## 工作台请求

工作台应在所有请求中携带登录接口返回的令牌：

```http
Authorization: Bearer <accessToken>
```

令牌存在时，后端只信任 JWT 中的用户 id：

- `GET /internal/user-model-profiles` 仅返回当前用户的配置。
- `GET`、`PUT`、`DELETE /internal/user-model-profiles/{profileId}` 只能操作当前用户拥有的配置。
- `POST /internal/user-model-profiles` 使用当前用户创建配置，请求体的 `userId` 会被忽略。
- `POST /internal/user-model-profiles/resolve` 同样以 JWT 用户为准，请求体的 `userId` 不会越权影响解析结果。

不属于当前用户的配置统一按不存在处理，返回 `404`，避免暴露其真实归属。

## 运行时认证

Python runtime 通过 `X-Memo-Echo-Runtime-Token` 和 `X-Memo-Echo-User-Id` 调用解析接口，详细配置见 [runtime-service-auth.md](runtime-service-auth.md)。

旧的请求体 `userId` 仅可通过显式设置 `EVENT_CENTER_ALLOW_LEGACY_USER_HEADER=true` 临时兼容；默认严格模式不会接受该调用方式。
