import { useMemo } from "react";
import { marked } from "marked";
import DOMPurify from "dompurify";
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

/** 普通 AI 对话风格：无气泡盒，agent/系统消息按 markdown 渲染（支持任务汇报）。 */
export function MessageBubble({
  credential,
  message,
}: {
  credential: StoredCredential;
  message: ThreadMessage;
}) {
  const isUser = message.role === "user";
  const isSystem = message.role === "system";

  const markdownHtml = useMemo(() => {
    if (isUser || message.status === "pending" || message.status === "streaming") {
      return "";
    }
    try {
      return DOMPurify.sanitize(marked.parse(message.content || "", { async: false }));
    } catch {
      return "";
    }
  }, [isUser, message.content, message.status]);

  return (
    <article className={`ws-msg ${isUser ? "ws-msg-user" : ""} ${message.status === "error" ? "ws-msg-error" : ""}`}>
      <span className="ws-msg-avatar" aria-hidden="true">
        {isUser ? <User size={13} /> : isSystem ? <ShieldCheck size={13} /> : <Sparkle size={13} />}
      </span>
      <div className="ws-msg-body">
        <div className="ws-msg-meta">
          <strong>{isUser ? "你" : isSystem ? "系统" : "Memo Echo"}</strong>
          <time>{formatTime(message.createdAt)}</time>
        </div>
        {message.status === "pending" || message.status === "streaming" ? (
          <div className="ws-msg-streaming">
            <CircleNotch className="spinning" size={14} />
            <span>{message.content || (message.status === "streaming" ? "正在执行…" : "正在思考…")}</span>
          </div>
        ) : isUser ? (
          <p className="ws-msg-text ws-msg-text-plain">{message.content || "（空消息）"}</p>
        ) : (
          <div
            className="ws-msg-text ws-msg-markdown"
            dangerouslySetInnerHTML={{ __html: markdownHtml || message.content || "（无文本回复）" }}
          />
        )}
        {message.status === "needs_confirmation" && (
          <span className="ws-msg-confirm-hint">
            <ShieldCheck size={13} />本次结果需要你确认后会执行外部操作
          </span>
        )}
        {!isUser && message.status === "streaming" && message.liveTasks && message.liveTasks.length > 0 && (
          <LiveTaskCards tasks={message.liveTasks} />
        )}
        {!isUser && message.taskId && <TaskCardInline credential={credential} message={message} />}
      </div>
    </article>
  );
}