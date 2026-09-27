/* =============================================================================
 * sse.ts - 实时事件流（浏览器原生 EventSource）
 * -----------------------------------------------------------------------------
 * 后端在 /api/stream 上推这些命名事件：
 *   reply    某会话产出了回复（刷新对话流）
 *   report   某条消息命中了上报规则（队列页提示）
 *   draft    拟好了一条待确认草稿（队列页提示 + 可以一键确认）
 *   step     控制台一次执行里的某一步（工具调用/结果）—— 实时看见 agent 在干活
 *   run      控制台一次执行的开始/结束（状态与最终回复）
 *   progress 会话目标进度快照
 *   policy   会话策略在别处被改了（本页跟着刷新）
 *
 * SSE 是**全局广播**：没有按会话过滤的能力，所以每条事件都带 conversation_id，
 * 由订阅方自己判断"这条跟我有没有关系"。
 *
 * 为什么用 EventSource 而不是 WebSocket：服务端只会**单向推**，
 * 不需要客户端发消息；EventSource 自带断线重连（浏览器每 3 秒重试），
 * 少一个需要自己维护的重连状态机。
 *
 * 注意：token 不能放进 EventSource 的 header，所以这里靠同源 cookie/空 token。
 * 本机默认不配 token；配了 token 的场景下 SSE 需要反代层补 Authorization。
 * ========================================================================== */

export type StreamEvent =
  | { type: "reply"; data: { conversation_id: string; text: string } }
  | { type: "report"; data: Record<string, any> }
  | { type: "draft"; data: Record<string, any> }
  | { type: "step"; data: { conversation_id: string; run_id: string; step: any } }
  | { type: "run"; data: { conversation_id: string; run: any } }
  | { type: "progress"; data: { conversation_id: string; goal: any } }
  | { type: "policy"; data: Record<string, any> }
  | { type: "open" }
  | { type: "error" };

export function subscribe(
  onEvent: (event: StreamEvent) => void
): () => void {
  const source = new EventSource("/api/stream");

  const forward = (type: StreamEvent["type"]) => (event: MessageEvent) => {
    let data: any = {};
    try {
      data = event.data ? JSON.parse(event.data) : {};
    } catch {
      data = { raw: event.data };
    }
    onEvent({ type, data } as StreamEvent);
  };

  const handlers: [string, (event: MessageEvent) => void][] = [
    ["reply", forward("reply")],
    ["report", forward("report")],
    ["draft", forward("draft")],
    ["step", forward("step")],
    ["run", forward("run")],
    ["progress", forward("progress")],
    ["policy", forward("policy")],
  ];
  handlers.forEach(([name, handler]) => source.addEventListener(name, handler));
  source.onopen = () => onEvent({ type: "open" });
  source.onerror = () => onEvent({ type: "error" });

  return () => {
    handlers.forEach(([name, handler]) => source.removeEventListener(name, handler));
    source.close();
  };
}
