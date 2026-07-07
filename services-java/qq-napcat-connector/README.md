# qq-napcat-connector

这是 Memo Echo Agent 的 QQ 接入服务，负责两类能力：

1. 接收 NapCat Webhook
2. 作为内部适配器调用 NapCat HTTP API

## 当前职责

1. 接收 NapCat 上报的原始事件
2. 标准化为统一 `UnifiedEvent`
3. 转发到 `event-center-service`
4. 透传 `self_id`，供上层判断是否 `@机器人自己`
5. 提供内部接口调用 NapCat 常用 API

## 当前能力

- 接收群聊和私聊消息事件
- 解析文本、发送者、群号、提及对象
- 从消息段中提取附件信息
- 保留完整 `rawPayload`
- 按配置决定是否转发到 `event-center-service`
- 调用以下 NapCat API：
  - `get_login_info`
  - `get_status`
  - `send_group_msg`
  - `send_private_msg`
  - `get_group_list`
  - `get_group_member_list`
  - `get_group_msg_history`

## 本地启动

```bash
mvn spring-boot:run
```

默认端口：

- `8091`

Webhook 入口：

- `POST /api/connectors/qq/napcat/events`

内部联调入口：

- `GET /internal/napcat/login-info`
- `GET /internal/napcat/status`
- `POST /internal/napcat/messages/group`
- `POST /internal/napcat/messages/private`
- `GET /internal/napcat/groups`
- `GET /internal/napcat/groups/{groupId}/members`
- `GET /internal/napcat/groups/{groupId}/history?count=20`

发送纯文本示例：

```json
{
  "groupId": 138178088,
  "message": "这是一条测试消息"
}
```

发送消息段数组示例：

```json
{
  "groupId": 138178088,
  "segments": [
    {
      "type": "at",
      "data": {
        "qq": "3969785168"
      }
    },
    {
      "type": "text",
      "data": {
        "text": " 你好，今天下午14:00记得开会"
      }
    }
  ]
}
```

## 当前链路

```text
NapCat -> qq-napcat-connector -> event-center-service -> agent-runtime-python
```

## NapCat API 配置

```yaml
napcat:
  api:
    enabled: true
    base-url: http://127.0.0.1:3011
    token: 你的token
```

## 下一步建议

1. 增加富消息段发送能力
2. 接入文件上传与群公告接口
3. 给内部 API 增加更稳定的错误码映射
4. 把“发送通知”能力从 Python runtime 接到这里
