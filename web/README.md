# Memo Echo 值守控制台（web/）

浏览器端 UI。**不复用** `desktop-client/`（那是 Tauri 桌面壳），这一版是纯网页，
由 runtime 自己托管，开一个服务就能用。

## 跑起来

```powershell
# 生产（推荐日常用）：构建产物由 FastAPI 托管，和接口同源
cd web
npm install
npm run build
cd ..
uv run uvicorn app.main:app --host 127.0.0.1 --port 8000
# 浏览器打开 http://127.0.0.1:8000/
```

```powershell
# 开发：Vite dev server（5173），接口代理到 8000，改完即时热更
cd web
npm run dev          # 另开一个终端跑 runtime
```

`web/dist` 不存在时后端会跳过静态托管（不报错），所以没构建过也不影响接口。

## 四屏分别管什么

| 标签页 | 内容 | 对应后端 |
|---|---|---|
| 控制台 | 会话列表 + 对话流（实时 SSE）+ 右侧检查器（策略/关键词/人设/目标/攒批） | `/api/conversations*`、`/api/stream` |
| 上报队列 | 按通道分组：急事 / 请示 / 待确认草稿 / 重要 / 摘要；草稿可改后一键发出 | `/api/reports*`（含 `{id}/send`） |
| 设定集 | 多会话批量策略、按会话的工具授权、全局配置（白名单/别名）、建新会话 | `PATCH /api/conversations/{id}`、`/api/tools`、`/api/configs` |
| 运行状态 | 记忆健康度（解析失败=静默丢记忆）、攒批进度、整理结果、队列计数、存储占用 | `/api/memory/health`、`/api/storage`、`/api/reports/stats` |

## 设计取向（改样式前先读这段）

对着 Codex 那种"安静的深色工作台"做的，三条约束：

1. **层次靠玻璃与描边，不靠颜色**。面板是 `rgba(255,255,255,0.055)` + `backdrop-filter`，
   描边 1px、透明度 7.5%。彩色只留给**状态**（通道、健康度），不做装饰。
2. **毛玻璃需要背景有东西可糊**。`body::before` 那几团低饱和光晕是刻意的：
   纯黑底上 `backdrop-filter` 是看不见的。调色时别把环境光调暗到看不见。
   另外毛玻璃只用在**几块大面板**上（侧栏/检查器/输入区/卡片），
   铺满整页会掉帧。
3. **等宽字体只用于元信息**（ID、时间、计数、通道名）。正文用系统 UI 字体。

改配色只需要动 `src/styles/tokens.css`；组件样式都在 `src/styles/app.css`。

## 代码结构

```
src/
  lib/api.ts       所有 HTTP 调用与类型（唯一与后端说话的地方）
  lib/sse.ts       /api/stream 订阅（reply / report / draft 三类事件）
  lib/format.ts    展示层格式化（相对时间、通道名、会话显示名）
  components/      Sidebar + ui.tsx（徽标/开关/字段/按钮等零件）
  screens/         ConsoleScreen / QueueScreen / ProfilesScreen / HealthScreen
  App.tsx          外壳与标签页
```

**状态管理刻意保持"笨"**：一个 `refreshTick` 计数器 + 各屏自己拉数据，
SSE 事件到达就 `tick + 1`。这个体量下引状态库只会增加间接层，
真正麻烦的是"哪些数据什么时候失效"，用 tick 反而看得更清楚。

## 鉴权

本机默认不配 token。若服务端设了 `MEMO_ECHO_API_TOKEN`，
在浏览器控制台执行 `localStorage.setItem("memo_echo_token", "<token>")`。
注意 SSE（`EventSource`）不能带自定义头，配了 token 的场景下
`/api/stream` 需要反代层补 `Authorization`。
