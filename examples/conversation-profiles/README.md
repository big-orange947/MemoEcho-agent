# 会话设定集联调示例

下面的示例默认基于：

- `event-center-service` 端口 `8093`
- 你已经把 `JAVA_HOME` 切到 JDK 21

## 1. 查看默认种子数据

PowerShell:

```powershell
Invoke-RestMethod -Method GET -Uri "http://127.0.0.1:8093/internal/conversation-profiles"
```

## 2. 创建一个“私聊自动回复”设定

PowerShell:

```powershell
$body = @"
{
  "name": "特定联系人自动回复",
  "description": "演示：只对某个联系人自动回复",
  "enabled": true,
  "platform": "qq",
  "accountId": "3969785168",
  "scene": "life",
  "chatType": "private",
  "chatIds": ["2597164807"],
  "targetUserIds": ["10001"],
  "supportedRoutes": ["social_reply"],
  "triggerMode": "KEYWORD_ONLY",
  "triggerKeywords": ["紧急", "马上"],
  "personaMode": "PROMPT",
  "systemPrompt": "你现在模拟一个冷静、简洁、可靠的私人助理。",
  "skillReferences": ["skills/personas/reliable-assistant"],
  "modelProfileId": "model-profile-001",
  "preferredRoute": "social_reply",
  "replyMode": "AUTO_REPLY",
  "allowedTools": ["send_qq_message"],
  "requireHumanConfirmation": false,
  "priority": 30
}
"@

Invoke-RestMethod `
  -Method POST `
  -Uri "http://127.0.0.1:8093/internal/conversation-profiles" `
  -ContentType "application/json" `
  -Body $body
```

## 3. 测试匹配结果

PowerShell:

```powershell
$body = @"
{
  "platform": "qq",
  "accountId": "3969785168",
  "scene": "life",
  "chatType": "private",
  "chatId": "2597164807",
  "senderId": "10001",
  "senderRole": "",
  "route": "social_reply",
  "text": "这件事很紧急，麻烦马上回我",
  "atSelf": false
}
"@

Invoke-RestMethod `
  -Method POST `
  -Uri "http://127.0.0.1:8093/internal/conversation-profiles/match" `
  -ContentType "application/json" `
  -Body $body
```

## 4. 创建一个“群聊只在 @我 时生效”的设定

PowerShell:

```powershell
$body = @"
{
  "name": "群聊 @我 回应模式",
  "enabled": true,
  "platform": "qq",
  "accountId": "3969785168",
  "chatType": "group",
  "supportedRoutes": ["social_reply"],
  "triggerMode": "AT_SELF_ONLY",
  "personaMode": "PROMPT",
  "systemPrompt": "你是群聊里的轻量助手，被点名时才回应。",
  "preferredRoute": "social_reply",
  "replyMode": "AUTO_REPLY",
  "priority": 20
}
"@

Invoke-RestMethod `
  -Method POST `
  -Uri "http://127.0.0.1:8093/internal/conversation-profiles" `
  -ContentType "application/json" `
  -Body $body
```

## 5. 创建一个“只出草稿”的设定

PowerShell:

```powershell
$body = @"
{
  "name": "私聊草稿模式",
  "enabled": true,
  "platform": "qq",
  "accountId": "3969785168",
  "chatType": "private",
  "supportedRoutes": ["social_reply"],
  "triggerMode": "ALWAYS",
  "personaMode": "PROMPT",
  "systemPrompt": "只需要给出可发送草稿，不自动发送。",
  "preferredRoute": "social_reply",
  "replyMode": "DRAFT_ONLY",
  "priority": 10
}
"@

Invoke-RestMethod `
  -Method POST `
  -Uri "http://127.0.0.1:8093/internal/conversation-profiles" `
  -ContentType "application/json" `
  -Body $body
```

## 6. 删除某条设定

PowerShell:

```powershell
Invoke-RestMethod `
  -Method DELETE `
  -Uri "http://127.0.0.1:8093/internal/conversation-profiles/<profile-id>"
```

## 7. 关闭默认种子数据

修改：

```yaml
event-center:
  conversation-profiles:
    seed-defaults: false
```

然后重启 `event-center-service`。

## 8. 这套字段适合怎么用

推荐前端按下面的思路做表单：

- 范围字段
  - `platform`
  - `accountId`
  - `scene`
  - `chatType`
  - `chatIds`
  - `targetUserIds`
  - `supportedRoutes`

- 行为字段
  - `triggerMode`
  - `triggerKeywords`
  - `replyMode`
  - `requireHumanConfirmation`
  - `priority`

- Agent 增强字段
  - `systemPrompt`
  - `skillReferences`
  - `modelProfileId`
  - `allowedTools`
