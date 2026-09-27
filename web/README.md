# Memo Echo 值守控制台（web/）

浏览器端 UI。**不复用** `desktop-client/`（那是 Tauri 桌面壳），这一版是纯网页，
由 runtime 自己托管，开一个服务就能用。

## 跑起来

所有命令都**先 cd 到仓库根**（下面用绝对路径写，直接复制即可）：

```powershell
# 生产（推荐日常用）：构建产物由 FastAPI 托管，和接口同源
cd D:\project\memo-echo-v2\web
npm install          # 首次；之后改前端只需 npm run build
npm run build

cd D:\project\memo-echo-v2
uv run uvicorn app.main:app --host 127.0.0.1 --port 8000
# 浏览器打开 http://127.0.0.1:8000/
```

```powershell
# 开发：Vite dev server（5173），接口代理到 8000，改完即时热更
cd D:\project\memo-echo-v2\web
npm run dev          # 另开一个终端跑 runtime
```

`web/dist` 不存在时后端会跳过静态托管（不报错），所以没构建过也不影响接口。

## 六个标签页分别管什么

两个空间别搞混：**控制台**是你和 agent 的对话（派活），**会话**是 agent 替你盯着的 QQ 好友/群（值守）。

| 标签页 | 内容 | 对应后端 |
|---|---|---|
| 控制台 | **自然语言入口**：左侧是"对话"(线程)，中间和 agent 对话 + 每条指令的执行轨迹卡（工具调用/参数/结果/失败），右侧是对话设置 | `/api/threads*`、`/api/runs/*`、`/api/stream`（`step`/`run` 事件） |
| 会话 | 某个 QQ 好友/群里发生了什么：消息流 + 值守策略检查器（监视/回复/上报/此刻）。手动直接发一句收在底部折叠区 | `/api/conversations*` |
| 上报队列 | 按通道分组：急事 / 请示 / 待确认草稿 / 重要 / 摘要；草稿可改后一键发出 | `/api/reports*`（含 `{id}/send`） |
| 通讯录 | QQ 好友/群清单 + 各自在本地的值守状态（未建会话/已建但全关/值守中），勾选后批量建会话套策略 | `/api/contacts`、`/api/conversations/resolve` |
| 设定集 | 多会话批量策略、按会话的工具授权、全局配置（白名单/别名）、建新会话 | `PATCH /api/conversations/{id}`、`/api/tools`、`/api/configs` |
| 运行状态 | 记忆健康度（解析失败=静默丢记忆）、攒批进度、整理结果、队列计数、存储占用 | `/api/memory/health`、`/api/storage`、`/api/reports/stats` |

控制台这一屏的形态参照 `docs/workspace-chat-console.md`（Thread 是对话，Task 是执行），
在 v2 里的落地方式：一条对话就是一个 `platform=desktop/chat_type=thread` 的会话，
一次执行 = 一条 `agent_runs` + 若干 `agent_steps`。

## 设计取向（改样式前先读这段）

**极简浅色工作台**（Codex / Linear / Vercel 那种安静的浅色台面），四条硬规矩：

1. **层次靠 1px 描边与底色深浅**。页面 `#f6f7f8`、面板纯白、描边 `#e6e7ea`；
   不用毛玻璃、不用背景光晕、不堆多层投影（`--shadow` 只有一层很轻的）。
2. **彩色只用于状态**（急事/请示/草稿/健康/失败），装饰一律灰阶。
   强调色只有一个蓝 `--accent`；"我"的气泡与主按钮用近黑 `--ink`。
3. **可点的东西必须是 `button`/`a`**，不是 `div` 挂 `onClick` —— 键盘能 Tab 到、
   能聚焦、读屏器能念。`:focus-visible` 统一给焦点环，别用 `outline: none` 抹掉。
4. **数字与时间用等宽 + `tabular-nums`**，正文用系统 UI 字体；
   长文本一律 `truncate` 或 `overflow-wrap: anywhere`（侧栏名、工具参数最容易撑破）。

改配色只需要动 `src/styles/tokens.css`；组件样式都在 `src/styles/app.css`。
改完建议按 [Web Interface Guidelines](https://github.com/vercel-labs/web-interface-guidelines)
过一遍（本机已装同名 skill）。

## 代码结构

```
src/
  lib/api.ts       所有 HTTP 调用与类型（唯一与后端说话的地方）
  lib/sse.ts       /api/stream 订阅（reply / report / draft / step / run / progress / policy）
  lib/format.ts    展示层格式化（相对时间、通道名、会话显示名）
  components/      Sidebar（值守会话）+ ThreadSidebar（对话线程）+ ui.tsx 零件
  screens/         ConsoleScreen（派活）/ ChatScreen（值守）/ QueueScreen / ContactsScreen
                   / ProfilesScreen / HealthScreen
  App.tsx          外壳与标签页
```

**状态管理刻意保持"笨"**：一个 `refreshTick` 计数器 + 各屏自己拉数据，
SSE 事件到达就 `tick + 1`。唯一例外是控制台的执行轨迹 —— 它是流式追加的，
由 App 持有的 `liveRun` 状态维护（`step` 追加一步、`run` 更新终态），
这样"正在跑"的过程不用等重拉，界面也不会闪。

## 鉴权

本机默认不配 token。若服务端设了 `MEMO_ECHO_API_TOKEN`，
在浏览器控制台执行 `localStorage.setItem("memo_echo_token", "<token>")`。
注意 SSE（`EventSource`）不能带自定义头，配了 token 的场景下
`/api/stream` 需要反代层补 `Authorization`。
