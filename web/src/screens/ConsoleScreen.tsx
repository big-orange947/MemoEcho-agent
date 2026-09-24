/* =============================================================================
 * ConsoleScreen.tsx - 控制台：对话流 + 右侧检查器
 * -----------------------------------------------------------------------------
 * 这一屏回答两个问题：
 *   1. 这个会话里到底发生了什么（消息流，含 agent 的回复）；
 *   2. 我们在这个会话里的行为边界是什么（策略面板：监视/回复/上报/请示/工具）。
 *
 * 策略改动**立即生效**（点一下就打 PATCH），不做"保存"按钮 ——
 * 开关类配置多一步确认只会让人忘记自己改没改。改完把后端回报的
 * implied（被自动打开的开关）显式提示出来，不静默改语义。
 * ========================================================================== */

import { useEffect, useRef, useState } from "react";
import {
  Conversation,
  Goal,
  Message,
  listGoals,
  listMessages,
  patchConversation,
  sendMessage,
} from "../lib/api";
import { ACTOR_LABEL, REPLY_MODE_LABEL, relativeTime, stamp } from "../lib/format";
import { Badge, Button, Empty, Field, KV, Section, Switch } from "../components/ui";

export function ConsoleScreen({
  conversation,
  refreshTick,
  notify,
  onConversationChanged,
}: {
  conversation: Conversation | null;
  refreshTick: number;
  notify: (message: string, tone?: "info" | "danger") => void;
  onConversationChanged: () => void;
}) {
  const [messages, setMessages] = useState<Message[]>([]);
  const [goals, setGoals] = useState<Goal[]>([]);
  const [draft, setDraft] = useState("");
  const [sending, setSending] = useState(false);

  const bottomRef = useRef<HTMLDivElement | null>(null);
  const conversationId = conversation?.id || "";

  useEffect(() => {
    if (!conversationId) {
      setMessages([]);
      setGoals([]);
      return;
    }
    let alive = true;
    Promise.all([listMessages(conversationId), listGoals(conversationId)])
      .then(([loadedMessages, loadedGoals]) => {
        if (!alive) return;
        setMessages(loadedMessages);
        setGoals(loadedGoals);
      })
      .catch((error) => notify(`加载会话失败：${error.message}`, "danger"));
    return () => {
      alive = false;
    };
  }, [conversationId, refreshTick, notify]);

  useEffect(() => {
    bottomRef.current?.scrollIntoView({ block: "end" });
  }, [messages.length]);

  // 进行中的目标：贴在输入框上方 —— agent 在替号主办的事，得让人随时看得见
  const activeGoal = goals.find((goal) => goal.status === "active") || null;

  async function submit() {
    const text = draft.trim();
    if (!text || !conversationId || sending) return;
    setSending(true);
    try {
      await sendMessage(conversationId, text);
      setDraft("");
      // 回复走 SSE 推回来；这里先把用户那条拉出来，避免"点了没反应"的错觉
      const loaded = await listMessages(conversationId);
      setMessages(loaded);
      onConversationChanged();
    } catch (error: any) {
      notify(
        error.status === 429 ? "会话繁忙，稍后再试" : `发送失败：${error.message}`,
        "danger"
      );
    } finally {
      setSending(false);
    }
  }

  if (!conversation) {
    return (
      <div className="main">
        <Empty>
          左侧选一个会话。
          <br />
          没有会话时，去"设定集"用三元组先把要值守的会话建出来。
        </Empty>
      </div>
    );
  }

  const policy = conversation.policy;

  return (
    <div className="main">
      <div className="scroll">
        <div className="thread">
          {messages.length === 0 ? (
            <Empty>这个会话还没有消息。</Empty>
          ) : (
            messages.map((message) => (
              <div key={message.id} className="msg" data-role={message.role}>
                <div className="meta">
                  <span>
                    {message.role === "assistant"
                      ? ACTOR_LABEL.agent
                      : ACTOR_LABEL[message.source] || ACTOR_LABEL.contact}
                  </span>
                  <span>{stamp(message.created_at)}</span>
                  {message.source ? <span>· {message.source}</span> : null}
                </div>
                <div className="bubble">{message.content}</div>
              </div>
            ))
          )}
          <div ref={bottomRef} />
        </div>
      </div>

      <div className="composer">
        {activeGoal ? (
          <div className="row" style={{ gap: 8 }}>
            <Badge tone="normal">进行中</Badge>
            <span style={{ fontSize: 12 }}>{activeGoal.objective}</span>
            {activeGoal.progress ? (
              <span className="mono">· {activeGoal.progress}</span>
            ) : null}
          </div>
        ) : null}
        <textarea
          className="textarea"
          placeholder="以号主身份对这个会话说一句话（Ctrl/⌘ + Enter 发送）"
          value={draft}
          onChange={(event) => setDraft(event.target.value)}
          onKeyDown={(event) => {
            if ((event.metaKey || event.ctrlKey) && event.key === "Enter") {
              event.preventDefault();
              void submit();
            }
          }}
        />
        <div className="row between">
          <span className="mono">
            {policy.monitor ? "监视中" : "未监视"} · {REPLY_MODE_LABEL[policy.reply_mode]}
          </span>
          <Button variant="primary" onClick={submit} disabled={sending || !draft.trim()}>
            {sending ? "发送中…" : "发送"}
          </Button>
        </div>
      </div>
    </div>
  );
}

/* ---------------------------------------------------------------------------
 * 检查器：策略 + 目标
 * ------------------------------------------------------------------------- */
export function ConsoleInspector({
  conversation,
  onChanged,
  notify,
}: {
  conversation: Conversation | null;
  onChanged: () => void;
  notify: (message: string, tone?: "info" | "danger") => void;
}) {
  const [keywords, setKeywords] = useState("");
  const [persona, setPersona] = useState("");
  const [goals, setGoals] = useState<Goal[]>([]);

  const conversationId = conversation?.id || "";

  useEffect(() => {
    setKeywords((conversation?.policy.alert_keywords || []).join("、"));
    setPersona(conversation?.persona || "");
  }, [conversationId, conversation?.policy.alert_keywords, conversation?.persona]);

  useEffect(() => {
    if (!conversationId) {
      setGoals([]);
      return;
    }
    listGoals(conversationId).then(setGoals).catch(() => setGoals([]));
  }, [conversationId, conversation?.updated_at]);

  if (!conversation) {
    return <aside className="inspector" />;
  }

  const policy = conversation.policy;

  async function save(patch: Record<string, unknown>) {
    try {
      const result = await patchConversation(conversationId, patch);
      if ((result.implied || []).length) {
        notify(`已保存；顺手打开了：${result.implied.join("、")}`);
      } else {
        notify("已保存");
      }
      onChanged();
    } catch (error: any) {
      notify(`保存失败：${error.message}`, "danger");
    }
  }

  return (
    <aside className="inspector">
      <div className="scroll">
        <Section
          title="值守策略"
          actions={<span className="mono">{conversation.external_id}</span>}
        >
          <div className="stack">
            <Switch
              checked={policy.monitor}
              label="监视这个会话"
              hint="总开关：关闭则消息不落库、不写记忆、不上报、不回复"
              onChange={(next) => save({ monitor: next })}
            />
            <Field label="回复方式">
              <select
                className="input"
                value={policy.reply_mode}
                onChange={(event) => save({ reply_mode: event.target.value })}
              >
                {Object.entries(REPLY_MODE_LABEL).map(([value, label]) => (
                  <option key={value} value={value}>
                    {label}
                  </option>
                ))}
              </select>
            </Field>
            <Switch
              checked={policy.alert_enabled}
              label="重要消息上报"
              hint="命中规则的消息进上报队列；开了它会上报给号主"
              onChange={(next) => save({ alert_enabled: next })}
            />
            <Switch
              checked={policy.require_human_confirmation}
              label="拿不准先请示"
              hint="默认开。关掉只是少问，不越界的底线约束始终生效"
              onChange={(next) => save({ require_human_confirmation: next })}
            />
          </div>
        </Section>

        <Section title="上报关键词">
          <Field
            label="命中即候选（顿号或逗号分隔）"
            hint="排期变更这类没有疑问词的消息，靠关键词兜住"
          >
            <input
              className="input"
              value={keywords}
              placeholder="改时间、组会、截止"
              onChange={(event) => setKeywords(event.target.value)}
              onBlur={() =>
                save({
                  alert_keywords: keywords
                    .split(/[、,，\s]+/)
                    .map((item) => item.trim())
                    .filter(Boolean),
                })
              }
            />
          </Field>
        </Section>

        <Section title="注意事项 / 人设">
          <Field label="注入到系统提示（如「别答应晚上十点后的活动」）">
            <textarea
              className="textarea"
              value={persona}
              onChange={(event) => setPersona(event.target.value)}
              onBlur={() => {
                if (persona !== (conversation.persona || "")) save({ persona });
              }}
            />
          </Field>
        </Section>

        <Section
          title="目标"
          actions={<span className="mono">{goals.length}</span>}
        >
          {goals.length === 0 ? (
            <div className="hint">这个会话还没有任务目标。</div>
          ) : (
            <div className="stack">
              {goals.slice(0, 6).map((goal) => (
                <div key={goal.id} className="stack" style={{ gap: 6 }}>
                  <div className="row between">
                    <span style={{ fontSize: 12 }}>{goal.objective}</span>
                    <Badge
                      tone={
                        goal.status === "active"
                          ? "normal"
                          : goal.status === "done"
                            ? "ok"
                            : undefined
                      }
                    >
                      {goal.status === "active" ? "进行中" : goal.status === "done" ? "已完成" : "已放弃"}
                    </Badge>
                  </div>
                  {goal.progress ? (
                    <div className="hint" style={{ fontSize: 11 }}>
                      {goal.progress}
                    </div>
                  ) : null}
                  <div className="mono">{relativeTime(goal.updated_at || "")}</div>
                </div>
              ))}
            </div>
          )}
        </Section>

        <Section title="攒批记忆">
          <KV
            items={[
              ["触发条数", policy.digest_max_messages],
              ["空闲触发", `${Math.round(policy.digest_window_seconds / 60)} 分钟`],
              [
                "工具授权",
                policy.allowed_tools.length ? policy.allowed_tools.join("、") : "按会话类型默认",
              ],
            ]}
          />
        </Section>
      </div>
    </aside>
  );
}
