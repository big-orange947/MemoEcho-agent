# 平台连接档案

平台连接档案用于保存用户绑定的 QQ/NapCat 及未来微信、邮箱、办公平台连接。连接记录按用户隔离，凭据使用事件中心主密钥加密后保存，任何响应都不会返回明文或密文。

## 开发期用户上下文

当前接口通过请求头传递本地用户标识：

```http
X-Memo-Echo-User-Id: local-user
```

未传时默认使用 `local-user`。这是本地开发兼容通道。完成登录后应使用 `Authorization: Bearer <token>`，此时 Token 中的用户身份优先于该请求头。公开部署前应关闭 `EVENT_CENTER_ALLOW_LEGACY_USER_HEADER`。

## 接口

| 方法 | 路径 | 说明 |
| --- | --- | --- |
| `GET` | `/internal/connections` | 列出当前用户连接；首次访问会创建本地 QQ/NapCat 档案。 |
| `POST` | `/internal/connections` | 创建连接档案。 |
| `PUT` | `/internal/connections/{connectionId}` | 更新当前用户拥有的连接。凭据留空时保留原值。 |
| `POST` | `/internal/connections/{connectionId}/health` | 主动刷新账号信息和健康状态。 |
| `DELETE` | `/internal/connections/{connectionId}` | 删除连接和加密凭据。 |

创建示例：

```json
{
  "name": "我的 QQ",
  "platform": "qq",
  "connector": "napcat",
  "enabled": true,
  "connectorBaseUrl": "http://127.0.0.1:8091",
  "credential": "只写凭据"
}
```

响应使用 `hasCredential` 表示是否已配置凭据，不包含 `credential` 或 `credentialCiphertext`。当前 QQ 健康检查通过 Connector 的 `/internal/napcat/login-info` 和 `/internal/napcat/status` 完成；事件中心不会直接访问 NapCat Token。
