import { CircleNotch, ShieldCheck, WarningCircle, User, Sparkle } from "@phosphor-icons/react";
import type { StoredCredential, ThreadMessage } from "../../types";
import { LiveTaskCards, TaskCardInline } from "./TaskCardInline";

function formatTime(value: string) {
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return "";
  const now = new Date();
  const sameDay = date.toDateString() === now.toDateString();
  return sameDay
    ? date.toLocaleTimeString("zh-CN", { hour: "2-digit", minute: "2-digit" })
    : date.toLocaleString("zh-CN", { month: "2-digit", day: "2-digit", hour: "2-digit", minute: "2-digit" });
}

/** 单条对话消息：用户 / Agent / 系统气泡，含 pending、error、需确认状态与任务内嵌卡片。 */
export function MessageBubble({
  credential,
  message,
}: {
  credential: StoredCredential;
  message: ThreadMessage;
}) {
  const isUser = message.role === "user";
  const isSystem = message.role === "system";

  return (
    <article className={`ws-msg ws-msg-${message.role}`}>
      <span className="ws-msg-avatar" aria-hidden="true">
        {isUser ? <User size={15} /> : isSystem ? <ShieldCheck size={15} /> : <Sparkle size={15} />}
      </span>
      <div className="ws-msg-body">
        <div className="ws-msg-meta">
          <strong>{isUser ? "你" : isSystem ? "系统" : "Memo Echo"}</strong>
          <time>{formatTime(message.createdAt)}</time>
        </div>
        <div className={`ws-msg-bubble ${message.status === "error" ? "ws-msg-error" : ""}`}>
          {message.status === "pending" || message.status === "streaming" ? (
            <span className="ws-msg-pending">
              <CircleNotch className="spinning" size={14} />
              {message.content || (message.status === "streaming" ? "正在执行…" : "正在思考…")}
            </span>
          ) : (
            <p className="ws-msg-text">{message.content || "（无文本回复）"}</p>
          )}
          {message.status === "error" && <WarningCircle className="ws-msg-warn" size={15} />}
          {message.status === "needs_confirmation" && (
            <span className="ws-msg-confirm-hint">
              <ShieldCheck size={13} />本次结果需要你确认后会执行外部操作
            </span>
          )}
        </div>
        {!isUser && message.status === "streaming" && message.liveTasks && message.liveTasks.length > 0 && (
          <LiveTaskCards tasks={message.liveTasks} />
        )}
        {!isUser && message.taskId && <TaskCardInline credential={credential} message={message} />}
      </div>
    </article>
  );
}