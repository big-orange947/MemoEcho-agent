import { useEffect } from "react";
import { ArrowClockwise, ChatCircleDots, FileText, ImageSquare, X } from "@phosphor-icons/react";

import type { ConversationMessage, ConversationProgressSnapshot } from "../types";

type ConversationContextDialogProps = {
  open: boolean;
  contactName: string;
  platform: string;
  snapshot: ConversationProgressSnapshot | null;
  loading: boolean;
  error: string;
  onClose: () => void;
  onRetry: () => void;
  headerMeta?: string;
  summaryTitle?: string;
  summaryBadge?: string;
  loadingTitle?: string;
  loadingDescription?: string;
  emptyText?: string;
  highlightEventId?: string;
  highlightEventIds?: string[];
};

/**
 * 渲染按需加载的 QQ 式上下文弹窗。
 * 弹窗只展示 Event Center 已存档的真实消息，不会在打开或关闭时发送任何平台消息。
 */
export function ConversationContextDialog({
  open,
  contactName,
  platform,
  snapshot,
  loading,
  error,
  onClose,
  onRetry,
  headerMeta = "会话上下文",
  summaryTitle = "当前聊天进度",
  summaryBadge = "",
  loadingTitle = "正在获取当前聊天进度",
  loadingDescription = "正在读取双方最新消息，并生成本次会话的自然语言概括",
  emptyText = "暂无可展示的双方消息",
  highlightEventId = "",
  highlightEventIds = [],
}: ConversationContextDialogProps) {
  /** 允许用户按 Esc 关闭弹窗，避免只能依赖右上角按钮。 */
  useEffect(() => {
    if (!open) return undefined;
    const handleKeyDown = (event: KeyboardEvent) => {
      if (event.key === "Escape") onClose();
    };
    window.addEventListener("keydown", handleKeyDown);
    return () => window.removeEventListener("keydown", handleKeyDown);
  }, [open, onClose]);

  if (!open) return null;

  const messages = [...(snapshot?.messages || [])]
    .filter((message) => message.text?.trim() || message.attachments?.length)
    .sort((left, right) => parseTime(left.timestamp) - parseTime(right.timestamp));
  const contactAvatar = messages.find((message) => !isOwnMessage(message) && message.senderAvatar)?.senderAvatar || "";

  return (
    <div className="context-dialog-backdrop" onMouseDown={onClose}>
      <section
        className="context-dialog"
        role="dialog"
        aria-modal="true"
        aria-label={`${contactName} 的会话上下文`}
        onMouseDown={(event) => event.stopPropagation()}
      >
        <header className="context-dialog-header">
          <div>
            <MessageAvatar className="context-dialog-avatar" avatarUrl={contactAvatar} label={contactName} />
            <div>
              <b>{contactName}</b>
              <small>{platform.toUpperCase()} · {headerMeta}</small>
            </div>
          </div>
          <button type="button" onClick={onClose} aria-label="关闭上下文"><X size={18} /></button>
        </header>

        {loading ? (
          <div className="context-dialog-loading" aria-live="polite">
            <span className="context-loading-orbit"><ChatCircleDots size={25} /></span>
            <b>{loadingTitle}</b>
            <p>{loadingDescription}</p>
            <i><span /><span /><span /></i>
          </div>
        ) : error ? (
          <div className="context-dialog-error" role="alert">
            <ChatCircleDots size={26} />
            <b>当前上下文获取失败</b>
            <p>{error}</p>
            <button type="button" onClick={onRetry}><ArrowClockwise size={16} />重新获取</button>
          </div>
        ) : (
          <div className="context-dialog-content">
            <section className="context-progress-summary">
              <div>
                <small>{summaryTitle}</small>
                <span>{summaryBadge || (snapshot?.generatedByModel ? "Agent 总结" : "本地概括")}</span>
              </div>
              <p>{snapshot?.summary || "当前还没有可用于判断进度的消息"}</p>
              <time>{snapshot?.generatedAt ? `更新于 ${formatDateTime(snapshot.generatedAt)}` : ""}</time>
            </section>
            <div className="context-chat-window">
              {messages.length === 0 ? (
                <div className="context-chat-empty"><ChatCircleDots size={24} /><span>{emptyText}</span></div>
              ) : messages.map((message, index) => (
                <ConversationBubble
                  key={`${message.eventId}-${index}`}
                  message={message}
                  contactName={contactName}
                  highlighted={Boolean(
                    (highlightEventId && message.eventId === highlightEventId)
                    || highlightEventIds.includes(message.eventId),
                  )}
                />
              ))}
            </div>
          </div>
        )}
      </section>
    </div>
  );
}

/** 根据可信消息来源把本人和 Agent 已发送内容放到右侧，对方消息放到左侧。 */
function ConversationBubble({ message, contactName, highlighted }: { message: ConversationMessage; contactName: string; highlighted: boolean }) {
  const own = isOwnMessage(message);
  const agentSent = ["AGENT_AUTO", "AGENT_CONFIRMED"].includes((message.messageOrigin || "").toUpperCase());
  const attachment = message.attachments?.[0];
  const mediaSummary = message.mediaAnalysis?.find((item) => item.summary?.trim())?.summary;
  const body = message.text?.trim() || mediaSummary || describeAttachment(message);
  const imageUrl = attachment?.url && isImageAttachment(attachment.fileType, attachment.fileName)
    ? attachment.url
    : "";

  return (
    <article className={`context-message ${own ? "own" : "peer"} ${highlighted ? "source-highlight" : ""}`}>
      {!own && <MessageAvatar className="context-message-avatar" avatarUrl={message.senderAvatar || ""} label={message.senderName || contactName} />}
      <div>
        <small>{own ? (agentSent ? "Agent 代发" : "我") : (message.senderName || contactName)} · {formatDateTime(message.timestamp)}</small>
        <div className="context-message-bubble">
          {imageUrl && <img src={imageUrl} alt={attachment?.fileName || "聊天图片"} />}
          <p>{body}</p>
          {message.attachments?.length > 0 && !imageUrl && (
            <span className="context-attachment-label">
              {isImageAttachment(attachment?.fileType, attachment?.fileName) ? <ImageSquare size={15} /> : <FileText size={15} />}
              {attachment?.fileName || `${message.attachments.length} 个附件`}
            </span>
          )}
        </div>
      </div>
      {own && <MessageAvatar className={`context-message-avatar ${agentSent ? "agent" : ""}`} avatarUrl={message.senderAvatar || ""} label={agentSent ? "A" : "我"} />}
    </article>
  );
}

/** 渲染头像图片，并在网络图片失效时自然露出下层文字占位，不显示浏览器破图图标。 */
function MessageAvatar({ className, avatarUrl, label }: { className: string; avatarUrl: string; label: string }) {
  return <span className={className}>
    <i>{label.slice(0, 1) || "M"}</i>
    {avatarUrl && <img src={avatarUrl} alt="" onError={(event) => { event.currentTarget.style.display = "none"; }} />}
  </span>;
}

/** 使用 Event Center 的来源枚举判断消息方向，不能依赖昵称或群成员角色。 */
function isOwnMessage(message: ConversationMessage) {
  const origin = (message.messageOrigin || "").toUpperCase();
  return ["USER_MANUAL", "AGENT_AUTO", "AGENT_CONFIRMED"].includes(origin) || message.senderRole === "self";
}

/** 为没有文本的附件消息生成中性描述，避免前端臆测图片含义。 */
function describeAttachment(message: ConversationMessage) {
  if (!message.attachments?.length) return "发送了一条无文本消息";
  return message.attachments.length === 1 ? "发送了一个附件" : `发送了 ${message.attachments.length} 个附件`;
}

/** 判断附件是否适合在对话气泡中直接作为图片预览。 */
function isImageAttachment(fileType?: string | null, fileName?: string | null) {
  const type = (fileType || "").toLowerCase();
  const name = (fileName || "").toLowerCase();
  return type.includes("image") || /\.(png|jpe?g|gif|webp|bmp)$/.test(name);
}

/** 安全解析后端时间；异常时间放在最前面但仍保留原消息。 */
function parseTime(value: string) {
  const parsed = Date.parse(value);
  return Number.isNaN(parsed) ? 0 : parsed;
}

/** 统一弹窗中的本地时间格式，避免每条消息重复显示完整 ISO 字符串。 */
function formatDateTime(value: string) {
  const parsed = new Date(value);
  if (Number.isNaN(parsed.getTime())) return value || "时间未知";
  return new Intl.DateTimeFormat("zh-CN", {
    month: "numeric",
    day: "numeric",
    hour: "2-digit",
    minute: "2-digit",
  }).format(parsed);
}
