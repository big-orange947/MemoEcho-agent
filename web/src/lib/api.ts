/* =============================================================================
 * api.ts - 后端接口客户端（唯一与后端说话的地方）
 * -----------------------------------------------------------------------------
 * 为什么集中在一处：
 *   · 路径、错误处理、鉴权头只写一遍，改后端时只动这个文件；
 *   · 类型与后端归一化后的字段一一对应（策略里的 monitor/alert_enabled 等
 *     后端已经转成 bool，前端不再猜 0/1）。
 *
 * 鉴权：本机使用默认无 token；若服务端配了 MEMO_ECHO_API_TOKEN，
 * 用 localStorage 里的 memo_echo_token 带上 Bearer 头。
 * ========================================================================== */

export type ReplyMode = "off" | "draft" | "auto";
export type Lane = "urgent" | "normal" | "question" | "digest" | "draft";
export type ReportStatus =
  | "candidate"
  | "pending"
  | "claimed"
  | "acked"
  | "dropped"
  | "dead";

export interface Policy {
  monitor: boolean;
  reply_mode: ReplyMode;
  alert_enabled: boolean;
  alert_keywords: string[];
  require_human_confirmation: boolean;
  digest_window_seconds: number;
  digest_max_messages: number;
  allowed_tools: string[];
}

export interface Conversation {
  id: string;
  platform: string;
  chat_type: string;
  external_id: string;
  title: string;
  persona: string;
  model_name?: string;
  created_at?: string;
  updated_at?: string;
  last_message_at?: string;
  policy: Policy;
}

export interface Message {
  id: string;
  conversation_id: string;
  role: "user" | "assistant" | string;
  source: string;
  content: string;
  created_at: string;
  goal_id?: string;
}

export interface ReportRecord {
  id: string;
  lane: Lane | string;
  conversation_id: string;
  status: ReportStatus | string;
  attempts: number;
  payload: Record<string, any>;
  message_ids: string[];
  created_at: string;
  updated_at: string;
}

export interface Goal {
  id: string;
  conversation_id: string;
  objective: string;
  status: "active" | "done" | "abandoned" | string;
  progress: string;
  created_at?: string;
  updated_at?: string;
}

export interface ToolInfo {
  name: string;
  description: string;
  high_risk: boolean;
  tags: string[];
  default_for: { private: boolean; group: boolean };
}

export interface MemoryHealth {
  enabled: boolean;
  init_error: string;
  summary: Record<string, any>;
  batches: Record<string, any>[];
  consolidation?: Record<string, any>[];
}

export interface StorageStatus {
  files: Record<string, { bytes: number; human: string }>;
  rows: Record<string, number>;
  total_bytes: number;
  total_human: string;
  policy?: Record<string, any>;
}

/* ------------------------------------------------------------------ 底层 */
function token(): string {
  try {
    return localStorage.getItem("memo_echo_token") || "";
  } catch {
    return "";
  }
}

export class ApiError extends Error {
  status: number;
  constructor(status: number, message: string) {
    super(message);
    this.status = status;
  }
}

async function request<T>(
  path: string,
  init?: RequestInit & { json?: unknown }
): Promise<T> {
  const headers: Record<string, string> = { ...(init?.headers as any) };
  const bearer = token();
  if (bearer) headers["Authorization"] = `Bearer ${bearer}`;
  let body = init?.body;
  if (init?.json !== undefined) {
    headers["Content-Type"] = "application/json";
    body = JSON.stringify(init.json);
  }

  const response = await fetch(path, { ...init, headers, body });
  const text = await response.text();
  let parsed: any = null;
  try {
    parsed = text ? JSON.parse(text) : null;
  } catch {
    parsed = text;
  }
  if (!response.ok) {
    const detail =
      (parsed && (parsed.detail || parsed.message)) ||
      (typeof parsed === "string" ? parsed : "") ||
      `HTTP ${response.status}`;
    throw new ApiError(response.status, String(detail));
  }
  return parsed as T;
}

/* ------------------------------------------------------------------ 会话 */
export const listConversations = () =>
  request<Conversation[]>("/api/conversations");

export const getConversation = (id: string) =>
  request<Conversation>(`/api/conversations/${encodeURIComponent(id)}`);

export const listMessages = (id: string) =>
  request<Message[]>(`/api/conversations/${encodeURIComponent(id)}/messages`);

export const sendMessage = (id: string, text: string) =>
  request<{ conversation_id: string; event_id: string }>(
    `/api/conversations/${encodeURIComponent(id)}/messages`,
    { method: "POST", json: { text } }
  );

/** 改会话策略/档案。返回 changed（字段级差异）与 implied（被自动打开的开关）。 */
export const patchConversation = (id: string, patch: Record<string, unknown>) =>
  request<{ conversation: Conversation; changed: Record<string, any>; implied: string[] }>(
    `/api/conversations/${encodeURIComponent(id)}`,
    { method: "PATCH", json: patch }
  );

export const resolveConversation = (body: {
  platform: string;
  chat_type: string;
  external_id: string;
  title?: string;
}) =>
  request<Conversation>("/api/conversations/resolve", {
    method: "POST",
    json: body,
  });

export const listGoals = (conversationId: string) =>
  request<Goal[]>(`/api/conversations/${encodeURIComponent(conversationId)}/goals`);

/** 跨会话目标（后端新增接口） */
export const listRecentGoals = (params?: { status?: string; limit?: number }) => {
  const search = new URLSearchParams();
  if (params?.status) search.set("status", params.status);
  if (params?.limit) search.set("limit", String(params.limit));
  const suffix = search.toString() ? `?${search}` : "";
  return request<Goal[]>(`/api/goals${suffix}`);
};

export const createGoal = (conversationId: string, instruction: string) =>
  request<Goal>(`/api/conversations/${encodeURIComponent(conversationId)}/goal`, {
    method: "POST",
    json: { instruction },
  });

/* ------------------------------------------------------------------ 上报队列 */
export const listReports = (params?: {
  lane?: string;
  status?: string;
  conversation_id?: string;
  limit?: number;
}) => {
  const search = new URLSearchParams();
  Object.entries(params || {}).forEach(([key, value]) => {
    if (value !== undefined && value !== null && value !== "") {
      search.set(key, String(value));
    }
  });
  const suffix = search.toString() ? `?${search}` : "";
  return request<ReportRecord[]>(`/api/reports${suffix}`);
};

export const reportStats = () =>
  request<Record<string, number>>("/api/reports/stats");

export const ackReport = (id: string) =>
  request<{ ok: boolean }>(`/api/reports/${encodeURIComponent(id)}/ack`, {
    method: "POST",
    json: {},
  });

export const dropReport = (id: string, reason = "") =>
  request<{ ok: boolean }>(`/api/reports/${encodeURIComponent(id)}/drop`, {
    method: "POST",
    json: { reason },
  });

/** 把待确认草稿真正发出去（可带改过的文本）。失败时后端不会标记完成。 */
export const sendDraft = (id: string, text?: string) =>
  request<{ ok: boolean; message_id: string }>(
    `/api/reports/${encodeURIComponent(id)}/send`,
    { method: "POST", json: text ? { text } : {} }
  );

/* ------------------------------------------------------------------ 工具与配置 */
export const listTools = () => request<ToolInfo[]>("/api/tools");

export const listConfigs = () =>
  request<{ configs: Record<string, string> }>("/api/configs");

export const putConfig = (key: string, value: string) =>
  request<{ key: string; value: string }>(
    `/api/configs/${encodeURIComponent(key)}`,
    { method: "PUT", json: { value } }
  );

/* ------------------------------------------------------------------ 运行状态 */
export const memoryHealth = () => request<MemoryHealth>("/api/memory/health");

export const storageStatus = () => request<StorageStatus>("/api/storage");

export const eventStats = () => request<Record<string, number>>("/api/events/stats");
