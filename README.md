# Memo Echo Agent

Memo Echo Agent 是一个面向个人事务流的混合式 Agent 系统，目标是处理跨平台消息接入、任务提取、日程规划、文件理解和动作执行。

这个仓库采用 Java 与 Python 分层架构：

- Java 微服务负责连接器接入、事件接收、结构化业务服务和可靠执行
- Python Agent Runtime 负责路由、规划、领域 Agent 调度和工具编排

## 核心设计思想

- 用统一事件协议把平台接入和 Agent 工作流解耦
- 多个领域 Agent 由中央 Orchestrator 协同，而不是彼此自由调用
- 所有外部副作用都必须经过 Tool 层
- 结构化存储和向量检索可以组合使用，但不应反向侵入 Agent 逻辑

## 规划中的 Agent 领域

- Inbox Agent
- Schedule Agent
- Work Agent
- File Agent
- Social Agent
- GroupOps Agent

## 仓库结构

```text
docs/                    架构设计、工作流和路线图
services-java/           Java 微服务规划与服务契约
agent-runtime-python/    Python 调度器、规划器、Agent 和工具层
sdk/                     共享协议、Schema 与接口定义
examples/                示例事件与工作流样例
```

## 第一阶段目标

第一阶段先打通一条最小闭环：

1. 从 QQ Connector 接收消息和附件
2. 归一化为 `UnifiedEvent`
3. 先进入 Java `event-center-service` 做幂等与事件骨干分发
4. 再交给 Python Orchestrator 路由和调度
5. 提取日程和任务信息
6. 通过 Java 服务完成持久化
7. 把自然语言结果回传到原始平台

## 文档

- [架构设计](./docs/architecture.md)
- [协议设计](./docs/protocols.md)
- [Connector 设计](./docs/connectors.md)
- [工作流说明](./docs/workflows.md)
- [开发路线图](./docs/roadmap.md)
