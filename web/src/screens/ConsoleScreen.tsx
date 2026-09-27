/* =============================================================================
 * ConsoleScreen.tsx - 控制台: 和 agent 的对话(自然语言入口)
 * -----------------------------------------------------------------------------
 * 这一屏是什么:
 *   它**不是**"选一个 QQ 会话看聊天记录"(那是"会话"标签页干的事,而且直接开
 *   QQ 就行)。它是整个 agent runtime 的入口 —— 你用自然语言派活,agent 自己
 *   判断该找谁、该调什么工具、该发什么消息,并把**干活的过程**摊在你眼前:
 *
 *     [我]     帮我问一下 km 今晚有没有空打游戏
 *     [执行]   ① list_contacts            → 找到 3 个好友
 *              ② send_qq_message(25971…) → 已发送给联系人 2597164807
 *     [Agent]  已经问过 km 了,等他回。
 *
 * 两个关键设计:
 *   1. 执行轨迹是**一等公民**: 每条指令对应一次 run,每一步(工具调用、参数、
 *      结果、失败)都落库并实时推送 —— 没有它,界面就只是个聊天框,
 *      看不出 agent 到底做没做事。
 *   2. 消息与执行**在同一条流里**: 消息负责"说了什么",run 卡片挂在对应的
 *      那条指令后面负责"做了什么",两级数据都能独立重建(刷新页面不错位)。
 * ========================================================================== */

import { useEffect, useMemo, useRef, useState } from "react";
import {
  Message,
  Run,
  RunStep,
  Thread,
  deleteThread,
  listThreadMessages,
  listThreadRuns,
  sendInstruction,
  updateThread,
} from "../lib/api";
import { relativeTime, stamp } from "../lib/format";
import { Badge, Button, Empty, Field, Section } from "../components/ui";

const EXAMPLES = [
  "帮我问一下 km 今晚有没有空打游戏",
  "通知小号,明天七点上课",
  "看看最近有哪些重要消息",
];

/* ==========================================================================
 * 主屏
 * ======================================================================== */
export function ConsoleScreen({
  thread,
  refreshTick,
  notify,
  onChanged,
  liveRun,
  onLiveRun,
}: {
  thread: Thread | null;
  refreshTick: number;
  notify: (message: string, tone?: "info" | "danger") => void;
  onChanged: () => void;
  /** 正在跑的那次执行(由 App 从 SSE 事件维护),没有则为 null */
  liveRun: Run | null;
  onLiveRun: (run: Run | null) => void;
}) {
  const [messages, setMessages] = useState<Message[]>([]);
  const [runs, setRuns] = useState<Run[]>([]);
  const [draft, setDraft] = useState("");
  const [sending, setSending] = useState(false);
  const bottomRef = useRef<HTMLDivElement | null>(null);

  const threadId = thread?.id || "";

  /* ---------------------------------------------------------------- 数据加载 */
  useEffect(() => {
    if (!threadId) {
      setMessages([]);
      setRuns([]);
      return;
    }
    let alive = true;
    Promise.all([listThreadMessages(threadId), listThreadRuns(threadId)])
      .then(([loadedMessages, loadedRuns]) => {
        if (!alive) return;
        setMessages(loadedMessages);
        setRuns(loadedRuns);
        // 重开页面时可能还有一条在跑: 把它的进度接回来
        const running = [...loadedRuns].reverse().find((run) => run.status === "running");
        if (running) onLiveRun(running);
      })
      .catch((error) => notify(`加载对话失败：${error.message}`, "danger"));
    return () => {
      alive = false;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [threadId, refreshTick, notify]);

  // 把实时的 run 合并进列表(就地更新那条,别重复插入)
  useEffect(() => {
    if (!liveRun) return;
    setRuns((prev) =>
      prev.some((run) => run.id === liveRun.id)
        ? prev.map((run) => (run.id === liveRun.id ? liveRun : run))
        : [...prev, liveRun]
    );
  }, [liveRun]);

  // 执行结束: 重拉消息与轨迹,把 agent 的汇报落进时间线
  const finishedRunId = liveRun && liveRun.status !== "running" ? liveRun.id : "";
  useEffect(() => {
    if (!finishedRunId || !threadId) return;
    void Promise.all([listThreadMessages(threadId), listThreadRuns(threadId)])
      .then(([loadedMessages, loadedRuns]) => {
        setMessages(loadedMessages);
        setRuns(loadedRuns);
      })
      .catch(() => {});
    onLiveRun(null);
    onChanged();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [finishedRunId, threadId]);

  useEffect(() => {
    bottomRef.current?.scrollIntoView({ block: "end" });
  }, [messages.length, runs.length, liveRun?.steps.length]);

  /* ------------------------------------------------------------------ 时间线 */
  // 消息流是"说了什么"的真相;run 卡片按**下标的顺序**挂到对应用户消息后面
  // (两者由同一个入口按同序写入,所以按序配对是可靠的)。
  const timeline = useMemo(() => {
    const items: { key: string; role: string; content: string; created_at: string; run?: Run }[] = [];
    let cursor = 0;
    for (const message of messages) {
      const item = {
        key: message.id,
        role: message.role,
        content: message.content,
        created_at: message.created_at,
        run: undefined as Run | undefined,
      };
      if (message.role === "user" && cursor < runs.length) {
        item.run = runs[cursor];
        cursor += 1;
      }
      items.push(item);
    }
    // 刚发出、图还没落库的那条指令: 用 run 自己的 instruction 顶上
    for (let index = cursor; index < runs.length; index += 1) {
      items.push({
        key: `run-${runs[index].id}`,
        role: "user",
        content: runs[index].instruction,
        created_at: runs[index].started_at,
        run: runs[index],
      });
    }
    return items;
  }, [messages, runs]);

  const busy = Boolean(liveRun && liveRun.status === "running");

  /* ---------------------------------------------------------------- 发指令 */
  async function submit() {
    const text = draft.trim();
    if (!text || !threadId || sending) return;
    setSending(true);
    setDraft("");
    try {
      const result = await sendInstruction(threadId, text);
      // 乐观插入: 立刻显示指令 + "执行中"卡片,细节随后由 SSE 补齐
      onLiveRun({
        id: result.run_id,
        conversation_id: threadId,
        status: "running",
        instruction: text,
        reply: "",
        error: "",
        event_id: result.event_id,
        started_at: new Date().toISOString(),
        finished_at: "",
        steps: [],
      });
      onChanged();
    } catch (error: any) {
      setDraft(text); // 发失败把内容还给用户,别让人白打一遍
      notify(
        error.status === 429 ? "上一条还在跑，等它结束再发" : `发送失败：${error.message}`,
        "danger"
      );
    } finally {
      setSending(false);
    }
  }

  if (!thread) {
    return (
      <div className="main">
        <Empty>
          左侧点"+ 新建对话"开一条,或者选一条已有的。
          <br />
          这里是给 agent 派活的地方 —— 说清楚要办什么事,它自己去联系人、发消息。
        </Empty>
      </div>
    );
  }

  return (
    <div className="main">
      <div className="scroll">
        {/* data-layout=console: 我的指令在右、agent 在左（与值守会话相反） */}
        <div className="thread" data-layout="console">
          {timeline.length === 0 ? (
            <div className="console-hint">
              <div className="big">这条对话还是空的</div>
              <div className="hint">可以直接说的例子:</div>
              <ul className="examples">
                {EXAMPLES.map((example) => (
                  <li key={example}>
                    <button type="button" className="example" onClick={() => setDraft(example)}>
                      {example}
                    </button>
                  </li>
                ))}
              </ul>
            </div>
          ) : (
            timeline.map((item) => (
              <div key={item.key}>
                <div className="msg" data-role={item.role}>
                  <div className="meta">
                    <span>{item.role === "user" ? "我" : "Agent"}</span>
                    <span>{stamp(item.created_at)}</span>
                  </div>
                  <div className="bubble">{item.content}</div>
                </div>
                {item.run ? <RunCard run={item.run} /> : null}
              </div>
            ))
          )}
          <div ref={bottomRef} />
        </div>
      </div>

      <div className="composer">
        <div className="composer-inner">
          <textarea
            className="textarea"
            placeholder="说清楚要办什么（Enter 发送 / Shift+Enter 换行）"
            value={draft}
            onChange={(event) => setDraft(event.target.value)}
            onKeyDown={(event) => {
              if (event.key === "Enter" && !event.shiftKey) {
                event.preventDefault();
                void submit();
              }
            }}
          />
          <div className="row between" style={{ marginTop: 10 }}>
            <span className="mono">
              {busy ? "正在执行…" : "agent 会自己找人、自己发消息，过程显示在上面"}
            </span>
            <Button variant="primary" onClick={submit} disabled={sending || busy || !draft.trim()}>
              {busy ? "执行中…" : "派活"}
            </Button>
          </div>
        </div>
      </div>
    </div>
  );
}

/* ==========================================================================
 * 执行卡片: 一次指令跑了什么
 * ======================================================================== */
function RunCard({ run }: { run: Run }) {
  const [open, setOpen] = useState(run.status === "running");

  // 跑完自动收起: 多数时候只关心结果,想看细节再点开
  useEffect(() => {
    if (run.status !== "running") setOpen(false);
  }, [run.status]);

  const toolCalls = run.steps.filter((step) => step.kind === "tool_call").length;
  const toolNames = run.steps
    .filter((step) => step.kind === "tool_call")
    .map((step) => step.name)
    .filter(Boolean);
  const failed = run.steps.some((step) => !step.ok);

  return (
    <div className="run" data-status={run.status}>
      <button
        type="button"
        className="run-head"
        aria-expanded={open}
        onClick={() => setOpen(!open)}
      >
        <span className="caret">{open ? "▾" : "▸"}</span>
        <span className="run-title">执行过程</span>
        {run.status === "running" ? (
          <Badge tone="normal">执行中</Badge>
        ) : run.status === "error" ? (
          <Badge tone="danger">失败</Badge>
        ) : (
          <Badge tone={failed ? "warn" : "ok"}>{failed ? "完成（有失败步骤）" : "完成"}</Badge>
        )}
        {/* 收起时把这轮调了哪些工具摊出来 —— 否则这一条又宽又空 */}
        <span className="run-summary">
          {toolNames.length ? toolNames.join(" → ") : toolCalls ? `${toolCalls} 次工具调用` : "未调用工具"}
        </span>
        <span className="mono run-elapsed">{elapsed(run)}</span>
      </button>

      {open ? (
        <div className="run-body">
          {run.steps.length === 0 ? (
            <div className="step" data-kind="note">
              <span className="step-icon">…</span>
              <span className="step-detail">正在思考</span>
            </div>
          ) : (
            run.steps.map((step) => <StepRow key={step.id} step={step} />)
          )}
          {run.error ? (
            <div className="step" data-kind="error" data-ok="false">
              <span className="step-icon">!</span>
              <span className="step-name">执行失败</span>
              <span className="step-detail">{run.error}</span>
            </div>
          ) : null}
        </div>
      ) : null}
    </div>
  );
}

function StepRow({ step }: { step: RunStep }) {
  const [expanded, setExpanded] = useState(false);
  const long = step.detail.length > 90;
  const icon =
    step.kind === "tool_call" ? "→" : step.kind === "tool_result" ? (step.ok ? "✓" : "✗") : "•";

  return (
    <div className="step" data-kind={step.kind} data-ok={step.ok}>
      <span className="step-icon">{icon}</span>
      {step.name ? <span className="step-name">{step.name}</span> : null}
      <span className="step-detail">
        {long && !expanded ? `${step.detail.slice(0, 90)}…` : step.detail}
        {long ? (
          <button
            type="button"
            className="more"
            aria-expanded={expanded}
            onClick={() => setExpanded(!expanded)}
          >
            {expanded ? "收起" : "展开"}
          </button>
        ) : null}
      </span>
    </div>
  );
}

function elapsed(run: Run): string {
  const start = Date.parse(run.started_at);
  const end = run.finished_at ? Date.parse(run.finished_at) : Date.now();
  if (Number.isNaN(start) || Number.isNaN(end)) return "";
  const seconds = Math.max(0, Math.round((end - start) / 1000));
  if (seconds < 60) return `${seconds}s`;
  return `${Math.floor(seconds / 60)}m${seconds % 60}s`;
}

/* ==========================================================================
 * 检查器: 对话设置(重命名/归档/删除)
 * ======================================================================== */
export function ConsoleInspector({
  thread,
  notify,
  onChanged,
}: {
  thread: Thread | null;
  notify: (message: string, tone?: "info" | "danger") => void;
  onChanged: () => void;
}) {
  const [title, setTitle] = useState("");
  const [confirming, setConfirming] = useState(false);

  const threadId = thread?.id || "";
  useEffect(() => {
    setTitle(thread?.title || "");
    setConfirming(false);
  }, [threadId, thread?.title]);

  if (!thread) return <aside className="inspector" />;

  async function act(action: () => Promise<unknown>, done: string) {
    try {
      await action();
      notify(done);
      onChanged();
    } catch (error: any) {
      notify(`操作失败：${error.message}`, "danger");
    }
  }

  return (
    <aside className="inspector">
      <div className="scroll">
        <Section title="对话设置" actions={<span className="mono">{thread.id.slice(0, 6)}</span>}>
          <div className="stack">
            <Field label="标题" hint="只是给你自己看的名字">
              <input
                className="input"
                name="thread-title"
                autoComplete="off"
                value={title}
                placeholder="例如：组会安排"
                onChange={(event) => setTitle(event.target.value)}
                onBlur={() => {
                  if (title !== thread.title) {
                    void act(() => updateThread(threadId, { title }), "标题已改");
                  }
                }}
              />
            </Field>
            <div className="row" style={{ gap: 8 }}>
              <Button
                size="sm"
                onClick={() =>
                  void act(
                    () => updateThread(threadId, { archived: !thread.archived }),
                    thread.archived ? "已取消归档" : "已归档"
                  )
                }
              >
                {thread.archived ? "取消归档" : "归档"}
              </Button>
              <Button
                size="sm"
                variant={confirming ? "danger" : "ghost"}
                onClick={() => {
                  if (!confirming) {
                    setConfirming(true);
                    return;
                  }
                  void act(() => deleteThread(threadId), "对话已删除");
                }}
              >
                {confirming ? "确认删除?" : "删除"}
              </Button>
            </div>
            <div className="hint">
              归档只是收进抽屉(勾"显示已归档"能看到);删除会连消息与执行记录一起清掉,
              不影响任何 QQ 会话。
            </div>
          </div>
        </Section>

        <Section title="这条对话的用途">
          <div className="hint">
            你说的话在这里被当成指令,不是聊天。agent 会自己判断要不要联系别人、
            联系谁、说什么,并把它做的事显示在对话里。
          </div>
          <div className="mono" style={{ marginTop: 8 }}>
            创建于 {relativeTime(thread.created_at)}
          </div>
        </Section>
      </div>
    </aside>
  );
}
