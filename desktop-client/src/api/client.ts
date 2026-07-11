import type {
  AuthResponse,
  ConversationProfile,
  ConversationSummary,
  ModelProfile,
  ModelProfileDraft,
  PlatformConnection,
  PlatformConnectionDraft,
  QqContact,
  SkillDescriptor,
  SkillInstallResult,
  StoredCredential,
  WorkspaceCommandResponse,
} from "../types";

/** 归一化服务地址，避免用户输入末尾斜杠导致路径拼接错误。 */
function normalizeBaseUrl(baseUrl: string) {
  return baseUrl.trim().replace(/\/+$/, "");
}

/** 执行 JSON 请求，并将后端错误转成便于桌面端展示的异常。 */
async function request<T>(baseUrl: string, path: string, init: RequestInit = {}, token?: string): Promise<T> {
  const headers = new Headers(init.headers);
  headers.set("Content-Type", "application/json");
  if (token) headers.set("Authorization", `Bearer ${token}`);
  const response = await fetch(`${normalizeBaseUrl(baseUrl)}${path}`, { ...init, headers });
  if (!response.ok) {
    const body = await response.text();
    try {
      const payload = JSON.parse(body) as { message?: string; error?: string };
      throw new Error(payload.message || payload.error || `请求失败：HTTP ${response.status}`);
    } catch (error) {
      if (error instanceof SyntaxError) throw new Error(`请求失败：HTTP ${response.status}`);
      throw error;
    }
  }
  if (response.status === 204) return undefined as T;
  return response.json() as Promise<T>;
}

/** 调用本地注册接口并返回登录令牌。 */
export function register(baseUrl: string, username: string, password: string, displayName: string) {
  return request<AuthResponse>(baseUrl, "/api/auth/register", {
    method: "POST",
    body: JSON.stringify({ username, password, displayName }),
  });
}

/** 调用本地登录接口并返回登录令牌。 */
export function login(baseUrl: string, username: string, password: string) {
  return request<AuthResponse>(baseUrl, "/api/auth/login", {
    method: "POST",
    body: JSON.stringify({ username, password }),
  });
}

/** 检查 event-center 是否在线，供登录页在提交前快速诊断。 */
export async function checkHealth(baseUrl: string) {
  const response = await fetch(`${normalizeBaseUrl(baseUrl)}/actuator-like/health`);
  if (!response.ok) throw new Error(`服务不可用：HTTP ${response.status}`);
}

/** 读取当前用户拥有的平台连接，不会返回凭据明文。 */
export function listConnections(credential: StoredCredential) {
  return request<PlatformConnection[]>(credential.baseUrl, "/internal/connections", {}, credential.accessToken);
}

/** 读取当前用户拥有的模型配置，API Key 仅以脱敏形式返回。 */
export function listModelProfiles(credential: StoredCredential) {
  return request<ModelProfile[]>(credential.baseUrl, "/internal/user-model-profiles", {}, credential.accessToken);
}

/** 创建当前用户的模型配置，API Key 由 event-center 加密后持久化。 */
export function createModelProfile(credential: StoredCredential, draft: ModelProfileDraft) {
  return request<ModelProfile>(credential.baseUrl, "/internal/user-model-profiles", {
    method: "POST",
    body: JSON.stringify(draft),
  }, credential.accessToken);
}

/** 更新模型配置；apiKey 留空时后端会保留已有密钥。 */
export function updateModelProfile(credential: StoredCredential, profileId: string, draft: ModelProfileDraft) {
  const payload = { ...draft, apiKey: draft.apiKey.trim() || null };
  return request<ModelProfile>(credential.baseUrl, `/internal/user-model-profiles/${profileId}`, {
    method: "PUT",
    body: JSON.stringify(payload),
  }, credential.accessToken);
}

/** 删除当前用户拥有的一条模型配置。 */
export function deleteModelProfile(credential: StoredCredential, profileId: string) {
  return request<void>(credential.baseUrl, `/internal/user-model-profiles/${profileId}`, {
    method: "DELETE",
  }, credential.accessToken);
}

/** 读取最近会话摘要，为桌面端消息空间提供数据。 */
export function listConversations(credential: StoredCredential) {
  return request<ConversationSummary[]>(credential.baseUrl, "/internal/conversations", {}, credential.accessToken);
}

/** 读取会话设定集，用于展示当前已启用的人格、触发和通知规则。 */
export function listConversationProfiles(credential: StoredCredential) {
  return request<ConversationProfile[]>(credential.baseUrl, "/internal/conversation-profiles", {}, credential.accessToken);
}

/** 创建一条会话设定集规则，供桌面端把人格和回复策略保存到事件中心。 */
export function createConversationProfile(credential: StoredCredential, payload: Record<string, unknown>) {
  return request<ConversationProfile>(credential.baseUrl, "/internal/conversation-profiles", {
    method: "POST",
    body: JSON.stringify(payload),
  }, credential.accessToken);
}

/** 为当前登录用户创建平台连接，凭据只写入后端且不会出现在响应里。 */
export function createConnection(credential: StoredCredential, draft: PlatformConnectionDraft) {
  return request<PlatformConnection>(credential.baseUrl, "/internal/connections", {
    method: "POST",
    body: JSON.stringify({ ...draft, enabled: true }),
  }, credential.accessToken);
}

/** 主动检查一个平台连接的健康状态，例如确认 NapCat 是否仍可访问。 */
export function checkConnection(credential: StoredCredential, connectionId: string) {
  return request<PlatformConnection>(credential.baseUrl, `/internal/connections/${connectionId}/health`, {
    method: "POST",
  }, credential.accessToken);
}

/** 搜索当前 NapCat 账号下的好友和群聊，用于绑定精确会话范围。 */
export function searchQqContacts(credential: StoredCredential, keyword: string) {
  const params = new URLSearchParams();
  if (keyword.trim()) params.set("keyword", keyword.trim());
  const suffix = params.size ? `?${params.toString()}` : "";
  return request<QqContact[]>(credential.baseUrl, `/internal/contacts/qq${suffix}`, {}, credential.accessToken);
}

/** 读取本地已安装 Skill 清单，供会话设定选择复用。 */
export function listSkills(credential: StoredCredential) {
  return request<SkillDescriptor[]>(credential.baseUrl, "/internal/skills", {}, credential.accessToken);
}

/** 从 GitHub 引用安装 Skill，安装完成后可作为本地 Skill 被会话设定选择。 */
export function installGithubSkill(credential: StoredCredential, reference: string) {
  return request<SkillInstallResult>(credential.baseUrl, "/internal/skills/install/github", {
    method: "POST",
    body: JSON.stringify({ reference }),
  }, credential.accessToken);
}

/** 将桌面工作台的自然语言命令交给 event-center，由后端安全调用 Python Agent Runtime。 */
export function executeWorkspaceCommand(credential: StoredCredential, prompt: string, requestedRoute: string) {
  return request<WorkspaceCommandResponse>(credential.baseUrl, "/internal/workspace/commands", {
    method: "POST",
    body: JSON.stringify({ prompt, requestedRoute: requestedRoute || null }),
  }, credential.accessToken);
}
