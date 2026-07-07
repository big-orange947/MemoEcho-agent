# Connector 设计

本文档定义平台接入层的能力分级、职责边界以及 NapCat 的接入定位。

## 1. 为什么要单独设计 Connector

不同平台的能力差异非常大。

不能简单写成“支持某平台”，而要明确该平台支持哪些能力，例如：

- 能不能收消息
- 能不能拉历史
- 能不能收附件
- 能不能发消息
- 能不能做群操作
- 能不能接个人账号消息流

所以 Connector 必须是一个明确的能力层，而不是简单的 SDK 封装。

## 2. Connector 分类

### 2.1 Bot Connector

特点：

- 以机器人身份交互
- 往往不能拿到个人主账号完整收件箱
- 更适合问答、通知、群内指令

### 2.2 Workspace Connector

特点：

- 面向企业/团队工作空间
- 通常能拿到公告、文件、任务、日历等结构化数据
- 更适合工作流和办公代理

### 2.3 Personal Connector

特点：

- 面向个人账号消息流
- 可以接入私聊、群聊、联系人、附件等真实个人上下文
- 更适合个人事务代理

NapCat 在 QQ 这条线上，属于 Personal Connector 能力较强的一类。

## 3. Connector 能力矩阵

建议给每个平台都按能力项打标签。

### 基础能力

- `receive_message`
- `send_message`
- `read_history`
- `receive_attachment`
- `download_attachment`
- `get_contact_list`
- `get_group_list`

### 扩展能力

- `manage_group`
- `publish_notice`
- `friend_request_handle`
- `group_request_handle`
- `read_recent_contacts`
- `read_group_members`

### 高级能力

- `personal_inbox_access`
- `cross_device_file_access`
- `event_stream_push`

## 4. NapCat 的能力映射

基于当前 NapCat 文档，可以给出如下定位：

### 已具备

- `receive_message`
- `send_message`
- `read_history`
- `receive_attachment`
- `download_attachment`
- `get_contact_list`
- `get_group_list`
- `read_group_members`
- `publish_notice`
- `manage_group`
- `event_stream_push`
- `personal_inbox_access`

### 可用于 Agent 的功能方向

1. 消息收件箱整理
2. 群聊摘要
3. 日程提取
4. 文件驱动任务规划
5. 群运营与治理
6. 私聊待回复识别

## 5. Connector 的职责边界

Connector 负责：

- 原始协议接入
- 身份鉴权
- 原始事件解析
- 基础字段标准化
- 附件信息补齐
- 原始错误重试
- 把 `UnifiedEvent` 投递到事件骨干层

Connector 不负责：

- 复杂业务逻辑
- Agent 规划
- 多步骤编排
- 任务拆解
- 智能回复生成

## 6. Connector 输出要求

所有 Connector 最终都必须输出同一套 `UnifiedEvent`。

也就是说：

- NapCat 输出的是 `UnifiedEvent`
- 邮件 Connector 输出的也是 `UnifiedEvent`
- 飞书 Connector 输出的仍然是 `UnifiedEvent`

上层 Agent 逻辑不应感知平台差异。

## 7. 第一阶段建议

第一阶段先只做一个强 Connector：

- `qq-napcat-connector`

目标不是“支持很多平台”，而是先把：

- 消息
- 附件
- 事件投递
- 回传

这四条链打稳。
