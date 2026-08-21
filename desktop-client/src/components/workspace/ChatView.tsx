import { useEffect, useRef, useState } from "react";
import { Sparkle, FileText, UsersThree, CalendarDots, DotsThree } from "@phosphor-icons/react";
import type { StoredCredential, ThreadMessage } from "../../types";
import { Composer } from "./Composer";
import { MessageBubble } from "./MessageBubble";

const CAPABILITIES = [
  { label: "委托任务", prompt: "帮我和 km 约明天晚上打游戏，先问 km 几点有空，确定时间后再告诉小号", icon: <Sparkle size={18} /> },
  { label: "文档处理", prompt: "帮我解析并整理一份文档", icon: <FileText size={18} /> },
  { label: "群聊摘要", prompt: "总结我离开期间的重要群聊消息", icon: <UsersThree size={18} /> },
  { label: "日程规划", prompt: "根据最近消息规划今天的日程", icon: <CalendarDots size={18} /> },
  { label: "更多能力", prompt: "展示当前可以使用的能力", icon: <DotsThree size={18} /> },
];

/** 聊天流：消息列表、空态能力入口与底部输入框。 */
export function ChatView({
  credential,
  messages,
  sending,
  error,
  onSend,
}: {
  credential: StoredCredential;
  messages: ThreadMessage[];
  sending: boolean;
  error: string;
  onSend: (content: string) => void;
}) {
  const [draft, setDraft] = useState("");
  const listRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    const container = listRef.current;
    if (container) container.scrollTop = container.scrollHeight;
  }, [messages.length, sending]);

  function send(content: string) {
    const text = (content || "").trim();
    if (!text || sending) return;
    setDraft("");
    onSend(text);
  }

  return (
    <div className="ws-chat">
      <div className="ws-chat-list" ref={listRef}>
        {error && <div className="ws-chat-notice ws-chat-notice-error">{error}</div>}
        {!error && messages.length === 0 && !sending && (
          <div className="ws-chat-empty">
            <p>向 Memo Echo 委托你想处理的事情</p>
            <div className="ws-chat-capabilities">
              {CAPABILITIES.map((item) => (
                <button key={item.label} type="button" onClick={() => send(item.prompt)}>
                  {item.icon}
                  <span>{item.label}</span>
                </button>
              ))}
            </div>
          </div>
        )}
        {messages.map((message) => (
          <MessageBubble key={message.id} credential={credential} message={message} />
        ))}
        {sending && (
          <MessageBubble
            credential={credential}
            message={{
              id: "pending-agent",
              threadId: "",
              role: "agent",
              content: "正在处理…",
              status: "pending",
              executionId: "",
              taskId: null,
              workflowId: null,
              resultJson: null,
              createdAt: new Date().toISOString(),
            }}
          />
        )}
      </div>
      <Composer value={draft} onChange={setDraft} onSend={() => send(draft)} busy={sending} />
    </div>
  );
}