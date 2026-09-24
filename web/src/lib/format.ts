/* =============================================================================
 * format.ts - 展示层的格式化（时间、通道、状态）
 * -----------------------------------------------------------------------------
 * 只做"给人看"的转换；不做业务判断（那是后端的事）。
 * ========================================================================== */

export const LANE_LABEL: Record<string, string> = {
  urgent: "急事",
  normal: "重要",
  question: "请示",
  digest: "摘要",
  draft: "待确认草稿",
};

export const LANE_TONE: Record<string, string> = {
  urgent: "urgent",
  normal: "normal",
  question: "question",
  digest: "digest",
  draft: "draft",
};

export const STATUS_LABEL: Record<string, string> = {
  candidate: "待复核",
  pending: "待消费",
  claimed: "已认领",
  acked: "已处理",
  dropped: "已丢弃",
  dead: "死信",
};

export const REPLY_MODE_LABEL: Record<string, string> = {
  off: "不回复",
  draft: "草稿待确认",
  auto: "自动回复",
};

export const ACTOR_LABEL: Record<string, string> = {
  owner: "号主",
  human_self: "号主",
  contact: "对方",
  peer: "对方",
  agent: "我",
  system: "系统",
};

/** 相对时间：刚刚 / 3 分钟前 / 2 小时前 / 昨天 21:30 / 09-20 21:30 */
export function relativeTime(iso: string): string {
  if (!iso) return "";
  const then = new Date(iso);
  if (Number.isNaN(then.getTime())) return "";
  const now = Date.now();
  const diff = now - then.getTime();
  const minute = 60_000;
  const hour = 60 * minute;
  const day = 24 * hour;

  if (diff < 0) return clock(then);
  if (diff < minute) return "刚刚";
  if (diff < hour) return `${Math.floor(diff / minute)} 分钟前`;
  if (diff < day) return `${Math.floor(diff / hour)} 小时前`;
  if (diff < 2 * day) return `昨天 ${clock(then)}`;
  if (diff < 7 * day) return `${Math.floor(diff / day)} 天前`;
  return `${pad(then.getMonth() + 1)}-${pad(then.getDate())} ${clock(then)}`;
}

export function clock(date: Date): string {
  return `${pad(date.getHours())}:${pad(date.getMinutes())}`;
}

export function stamp(iso: string): string {
  if (!iso) return "";
  const date = new Date(iso);
  if (Number.isNaN(date.getTime())) return iso.slice(0, 16);
  return `${pad(date.getMonth() + 1)}-${pad(date.getDate())} ${clock(date)}`;
}

function pad(value: number): string {
  return value < 10 ? `0${value}` : String(value);
}

/** 会话在列表里显示的名字：标题优先，其次"群/私聊 + 号码" */
export function conversationLabel(conversation: {
  title?: string;
  external_id?: string;
  chat_type?: string;
  platform?: string;
}): string {
  const title = (conversation.title || "").trim();
  if (title) return title;
  const external = (conversation.external_id || "").trim();
  if (!external) return conversation.platform === "desktop" ? "桌面线程" : "未命名会话";
  const prefix = conversation.chat_type === "group" ? "群 " : "";
  return `${prefix}${external}`;
}

/** 策略 → 列表上的小旗标（只显示"开着"的，避免一屏全是灰字） */
export function policyFlags(policy?: {
  monitor?: boolean;
  reply_mode?: string;
  alert_enabled?: boolean;
}): { kind: string; label: string }[] {
  if (!policy) return [];
  const flags: { kind: string; label: string }[] = [];
  if (policy.monitor) flags.push({ kind: "monitor", label: "监视" });
  if (policy.reply_mode === "auto") flags.push({ kind: "auto", label: "自动回" });
  if (policy.reply_mode === "draft") flags.push({ kind: "draft", label: "草稿" });
  if (policy.alert_enabled) flags.push({ kind: "alert", label: "上报" });
  return flags;
}
