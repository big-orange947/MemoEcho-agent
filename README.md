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

## 当前主控台命令链路

主控台里的自然语言命令不会由前端或 Java 用规则直接创建任务。当前链路是：

1. Desktop Client 把用户命令提交到 Java `event-center-service`
2. Java 侧完成本地用户鉴权、基础权限校验、风险审计、事件落库和转发
3. Python Agent Runtime 接收命令，并由 Router Agent 识别目标联系人、任务类型和执行模式
4. Delegated Task Workflow 编译结构化任务契约，必要时为多个联系人拆成多个任务实例
5. Runtime 通过工具执行任务，例如查询联系人、读取上下文、发送 QQ 消息、更新任务状态和结束任务
6. 审查 Agent 结合任务、历史消息、Skill 和记忆判断候选回复是否适合发送

因此，Java 是可靠事件与权限边界，Python 是智能编排与任务创建中心。详细说明见 [主控台命令链路](./docs/workspace-commands.md)。

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

## 本地一键启动

本地启动脚本会管理 Event Center、Schedule Service、Task Service、Python Agent Runtime 和 QQ Connector。MySQL 与 NapCat 属于外部基础设施，需要提前启动，脚本不会修改或停止它们。

### 环境要求

- JDK 21，并确保 `java` 和 `mvn` 已加入 `PATH`
- Python 3.11 或更高版本
- MySQL 默认监听 `127.0.0.1:3306`
- NapCat WebUI 默认监听 `127.0.0.1:6099`，OneBot 网络配置由扫码流程自动创建

首次运行时安装 Python Runtime：

```powershell
python -m pip install -e .\agent-runtime-python
```

复制本机配置模板并填写三个数据库密码：

```powershell
Copy-Item .\scripts\local-env.example.ps1 .\scripts\local-env.ps1
notepad .\scripts\local-env.ps1
```

`scripts/local-env.ps1` 已被 Git 忽略，不要把 API Key、数据库密码或 NapCat Token 写入受版本控制的配置文件。

### QQ 扫码接入

先启动 NapCat，再启动 Memo Echo 的 Java 服务和桌面客户端。进入客户端“连接管理”，点击“扫码连接 QQ”，使用手机 QQ 扫描弹窗中的二维码即可。登录成功后，Connector 会自动完成以下工作：

- 创建供 Java Connector 调用的 OneBot HTTP Server
- 创建向 Memo Echo 上报消息的 HTTP Client
- 开启自身消息上报，以保留完整私聊上下文
- 刷新客户端中的 QQ 昵称、账号和在线状态

原生安装会自动检查各磁盘根目录下的 `napcat/config/webui.json`，Docker 部署会自动从 NapCat 容器读取 WebUI Token，因此通常无需手工复制端口和 Token。只有非标准安装目录、远程部署或自动发现失败时，才需要在 `scripts/local-env.ps1` 中配置 `NAPCAT_NATIVE_CONFIG_PATHS` 或 `NAPCAT_WEBUI_TOKEN`；旧的手工配置入口仍保留在客户端“高级手工配置”中用于排障。

### 启动与停止

```powershell
# 构建并启动全部应用服务
.\scripts\start-local.ps1

# 查看基础设施和应用服务状态
.\scripts\status-local.ps1

# 只停止本次脚本启动的应用服务
.\scripts\stop-local.ps1
```

代码没有变化时可以跳过 Maven 构建：

```powershell
.\scripts\start-local.ps1 -SkipBuild
```

运行日志保存在 `.runtime/logs`。PID 与进程启动时间记录在 `.runtime/local-processes.json`，停止脚本会同时校验 PID、进程名和启动时间，避免误停其他程序。

## 文档

- [架构设计](./docs/architecture.md)
- [协议设计](./docs/protocols.md)
- [Connector 设计](./docs/connectors.md)
- [工作流说明](./docs/workflows.md)
- [主控台命令链路](./docs/workspace-commands.md)
- [会话设定集](./docs/conversation-profiles.md)
- [Skill 管理](./docs/skill-management.md)
- [开发路线图](./ROADMAP.md)
