import type {
  AuthResponse,
  ConversationProfile,
  ConversationCognitionCard,
  ConversationDigestBatch,
  ConversationProgressSnapshot,
  ConversationSummary,
  ConversationProxyTaskState,
  ModelProfile,
  ModelProfileDraft,
  MemoryCandidate,
  MemoryCandidateEvidence,
  MemoryConflictResolution,
  MemoryCandidateDraft,
  NapcatQrLoginState,
  PlatformConnection,
  PlatformConnectionDraft,
  QceImportPreview,
  QceImportResult,
  QqContact,
  SkillDescriptor,
  SkillInstallResult,
  SkillResolvePreview,
  StoredEventDetail,
  StoredCredential,
  WorkspaceCommandResponse,
  DelegatedTask,
  WorkspaceBriefing,
  WorkspaceInbox,
  PendingGroupOperation,
  GroupOperationApprovalResult,
  WorkspaceScheduleDigest,
  WorkspaceScheduleDraft,
  WorkspaceScheduleSourceContext,
  SecureAsset,
  SecureAssetDraft,
  Thread,
  ThreadPatch,
  ThreadMessage,
  ThreadMessageSendResult,
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

/** 启动 NapCat 扫码登录，Token 发现和 OneBot 配置均由本机 Connector 完成。 */
export function startNapcatQrLogin(credential: StoredCredential) {
  return request<NapcatQrLoginState>(credential.baseUrl, "/internal/connections/qq/qr-login", {
    method: "POST",
  }, credential.accessToken);
}

/** 获取当前扫码状态；仅在二维码弹窗打开时由客户端轮询。 */
export function getNapcatQrLoginStatus(credential: StoredCredential) {
  return request<NapcatQrLoginState>(credential.baseUrl, "/internal/connections/qq/qr-login/status", {}, credential.accessToken);
}

/** 二维码失效后请求 NapCat 生成新二维码。 */
export function refreshNapcatQrLogin(credential: StoredCredential) {
  return request<NapcatQrLoginState>(credential.baseUrl, "/internal/connections/qq/qr-login/refresh", {
    method: "POST",
  }, credential.accessToken);
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

/** 读取当前用户的安全资产元数据；接口不会返回可用正文或数据库密文。 */
export function listSecureAssets(credential: StoredCredential) {
  return request<SecureAsset[]>(credential.baseUrl, "/internal/secure-assets", {}, credential.accessToken);
}

/** 创建安全资产；敏感正文会由 Event Center 加密后持久化。 */
export function createSecureAsset(credential: StoredCredential, draft: SecureAssetDraft) {
  return request<SecureAsset>(credential.baseUrl, "/internal/secure-assets", {
    method: "POST",
    body: JSON.stringify(draft),
  }, credential.accessToken);
}

/** 更新安全资产；content 为 null 时服务端保留原正文，不会用空值覆盖密文。 */
export function updateSecureAsset(credential: StoredCredential, assetId: string, draft: SecureAssetDraft) {
  return request<SecureAsset>(credential.baseUrl, `/internal/secure-assets/${encodeURIComponent(assetId)}`, {
    method: "PUT",
    body: JSON.stringify(draft),
  }, credential.accessToken);
}

/** 删除当前用户拥有的安全资产，服务端仍会执行所有权校验。 */
export function deleteSecureAsset(credential: StoredCredential, assetId: string) {
  return request<void>(credential.baseUrl, `/internal/secure-assets/${encodeURIComponent(assetId)}`, {
    method: "DELETE",
  }, credential.accessToken);
}

/** 读取最近会话摘要，为桌面端消息空间提供数据。 */
export function listConversations(credential: StoredCredential) {
  return request<ConversationSummary[]>(credential.baseUrl, "/internal/conversations", {}, credential.accessToken);
}

/**
 * 用户打开“查看上下文”弹窗时才请求会话快照。
 * 后端会在同一次响应中返回双方消息和自然语言进度，普通页面刷新不会触发模型分析。
 */
export function getConversationProgress(
  credential: StoredCredential,
  platform: string,
  chatType: string,
  chatId: string,
  limit = 60,
  lastSeenAgentEventId = "",
) {
  const params = new URLSearchParams({ platform, chatType, limit: String(limit) });
  if (lastSeenAgentEventId) params.set("lastSeenAgentEventId", lastSeenAgentEventId);
  return request<ConversationProgressSnapshot>(
    credential.baseUrl,
    `/internal/workspace/conversations/${encodeURIComponent(chatId)}/progress?${params.toString()}`,
    {},
    credential.accessToken,
  );
}

/** 读取当前 JWT 用户的事件级收件箱，包含原消息、Agent 草稿和待处理状态。 */
export function listWorkspaceInbox(credential: StoredCredential, inboxStatus = "") {
  const params = new URLSearchParams({ limit: "100" });
  if (inboxStatus) params.set("inboxStatus", inboxStatus);
  return request<WorkspaceInbox>(
    credential.baseUrl,
    `/internal/workspace/inbox?${params.toString()}`,
    {},
    credential.accessToken,
  );
}

/** 读取当前登录用户某个事件的待审批群操作；响应中不会包含 Runtime 令牌。 */
export function getPendingGroupOperation(credential: StoredCredential, eventId: string) {
  return request<PendingGroupOperation>(
    credential.baseUrl,
    `/internal/workspace/group-operations/${encodeURIComponent(eventId)}`,
    {},
    credential.accessToken,
  );
}

/** 按事件提交确认短语，由 Event Center 校验事件归属后代理 Runtime 执行。 */
export function approvePendingGroupOperation(
  credential: StoredCredential,
  eventId: string,
  confirmationText: string,
) {
  return request<GroupOperationApprovalResult>(
    credential.baseUrl,
    `/internal/workspace/group-operations/${encodeURIComponent(eventId)}/approve`,
    { method: "POST", body: JSON.stringify({ confirmationText }) },
    credential.accessToken,
  );
}

/** 将用户编辑后的接管回复发送到原会话，Event Center 会记录人工发送审计。 */
export function confirmInboxDraft(credential: StoredCredential, eventId: string, message: string) {
  return request<unknown>(credential.baseUrl, `/internal/events/${encodeURIComponent(eventId)}/draft/confirm`, {
    method: "POST",
    body: JSON.stringify({ message, note: "desktop_human_handoff" }),
  }, credential.accessToken);
}

/** 人工发送后按用户选择继续或暂停该会话的 Agent 代理。 */
export function updateConversationAgentState(
  credential: StoredCredential,
  platform: string,
  chatType: string,
  chatId: string,
  continueAgent: boolean,
) {
  return request<{ continueAgent: boolean; updatedProfiles: number }>(credential.baseUrl, "/internal/conversation-profiles/agent-state", {
    method: "POST",
    body: JSON.stringify({ platform, chatType, chatId, continueAgent }),
  }, credential.accessToken);
}

/** 读取 Agent 判断为已完成、但仍等待用户决定是否结束代理的会话任务。 */
export function listPendingConversationTaskCompletions(credential: StoredCredential) {
  return request<ConversationProxyTaskState[]>(
    credential.baseUrl,
    "/internal/conversation-profiles/task-completion/pending",
    {},
    credential.accessToken,
  );
}

/** 批准时正式结束代理；拒绝时恢复任务推进，并保留已经完成的历史证据。 */
export function decideConversationTaskCompletion(
  credential: StoredCredential,
  profileId: string,
  chatId: string,
  approved: boolean,
) {
  return request<ConversationProxyTaskState>(
    credential.baseUrl,
    `/internal/conversation-profiles/${encodeURIComponent(profileId)}/task-completion/decision`,
    {
      method: "POST",
      body: JSON.stringify({ chatId, approved }),
    },
    credential.accessToken,
  );
}

/** 读取慢通道生成的真实摘要批次，消息空间不再直接铺开原始事件。 */
export function listConversationDigests(credential: StoredCredential, limit = 50) {
  return request<ConversationDigestBatch[]>(
    credential.baseUrl,
    `/internal/workspace/digests?limit=${limit}`,
    {},
    credential.accessToken,
  );
}

/** 读取工作台聚合简报，其中包含今日日程和建议起点，供消息空间形成统一信息视图。 */
export function getWorkspaceBriefing(credential: StoredCredential, senderId: string, userName: string) {
  const params = new URLSearchParams({
    senderId,
    userName,
    lookbackMinutes: "1440",
    conversationLimit: "8",
    taskLimit: "8",
    scheduleLimit: "8",
  });
  return request<WorkspaceBriefing>(
    credential.baseUrl,
    `/internal/workspace/briefing?${params.toString()}`,
    {},
    credential.accessToken,
  );
}

/** 手动创建当前登录用户的日程，来源会由后端可靠标记为本地手动创建。 */
export function createWorkspaceSchedule(credential: StoredCredential, draft: WorkspaceScheduleDraft) {
  return request<WorkspaceScheduleDigest>(credential.baseUrl, "/internal/workspace/schedules", {
    method: "POST",
    body: JSON.stringify(draft),
  }, credential.accessToken);
}

/** 删除当前登录用户拥有的日程；服务端仍会执行所有权校验。 */
export function deleteWorkspaceSchedule(credential: StoredCredential, scheduleId: string) {
  return request<void>(
    credential.baseUrl,
    `/internal/workspace/schedules/${encodeURIComponent(scheduleId)}`,
    { method: "DELETE" },
    credential.accessToken,
  );
}

/** 按需读取日程的原始消息及其前后上下文，避免首屏加载整段聊天记录。 */
export function getWorkspaceScheduleSourceContext(
  credential: StoredCredential,
  scheduleId: string,
  radius = 3,
) {
  return request<WorkspaceScheduleSourceContext>(
    credential.baseUrl,
    `/internal/workspace/schedules/${encodeURIComponent(scheduleId)}/source-context?radius=${radius}`,
    {},
    credential.accessToken,
  );
}

/** 读取会话设定集，用于展示当前已启用的人格、触发和通知规则。 */
export function listConversationProfiles(credential: StoredCredential) {
  return request<ConversationProfile[]>(credential.baseUrl, "/internal/conversation-profiles", {}, credential.accessToken);
}

/** 按会话增量刷新认知卡；服务端会在来源消息未变化时直接复用旧结果。 */
export function refreshConversationCognition(
  credential: StoredCredential,
  platform: string,
  chatType: string,
  chatId: string,
  limit = 80,
) {
  const query = new URLSearchParams({ platform, chatType, chatId, limit: String(limit) });
  return request<ConversationCognitionCard>(
    credential.baseUrl,
    `/internal/conversation-cognition/refresh?${query.toString()}`,
    { method: "POST" },
    credential.accessToken,
  );
}

/** 读取当前用户的长期记忆候选与确认结果，可由客户端按状态分组展示。 */
export function listMemoryCandidates(credential: StoredCredential) {
  return request<MemoryCandidate[]>(credential.baseUrl, "/internal/memories", {}, credential.accessToken);
}

/** 手工建立一条候选记忆；服务端仍要求用户再执行一次确认。 */
export function createMemoryCandidate(credential: StoredCredential, draft: MemoryCandidateDraft) {
  return request<MemoryCandidate>(credential.baseUrl, "/internal/memories", {
    method: "POST",
    body: JSON.stringify({
      ...draft,
      expiresAt: draft.expiresAt ? new Date(draft.expiresAt).toISOString() : null,
      sourceEventIds: [],
      sourceActorType: "OWNER",
      factAuthority: "human_self",
      confidence: 1,
    }),
  }, credential.accessToken);
}

/** 修改仍处于候选状态的长期记忆，已确认或已归档记录不允许直接覆盖。 */
export function updateMemoryCandidate(credential: StoredCredential, id: string, draft: MemoryCandidateDraft) {
  return request<MemoryCandidate>(credential.baseUrl, `/internal/memories/${id}`, {
    method: "PUT",
    body: JSON.stringify({
      ...draft,
      expiresAt: draft.expiresAt ? new Date(draft.expiresAt).toISOString() : null,
      sourceEventIds: [],
      sourceActorType: "OWNER",
      factAuthority: "human_self",
      confidence: 1,
    }),
  }, credential.accessToken);
}

/** 用户确认候选事实；这是候选进入 Runtime 的唯一客户端入口。 */
export function verifyMemoryCandidate(credential: StoredCredential, id: string) {
  return request<MemoryCandidate>(credential.baseUrl, `/internal/memories/${id}/verify`, {
    method: "POST",
  }, credential.accessToken);
}

/** 按需读取候选记忆的来源聊天窗口，不会一次性下载完整会话历史。 */
export function getMemoryCandidateEvidence(credential: StoredCredential, id: string, radius = 3) {
  return request<MemoryCandidateEvidence>(
    credential.baseUrl,
    `/internal/memories/${encodeURIComponent(id)}/evidence?radius=${radius}`,
    {},
    credential.accessToken,
  );
}

/** 按事件读取 Runtime 执行审计信息，用于展示本次实际注入了哪些已确认长期记忆。 */
export function getStoredEventDetail(credential: StoredCredential, eventId: string) {
  return request<StoredEventDetail>(
    credential.baseUrl,
    `/internal/events/${encodeURIComponent(eventId)}`,
    {},
    credential.accessToken,
  );
}

/** 原子处理候选与已确认值的冲突，避免客户端连续写入造成双重事实。 */
export function resolveMemoryConflict(
  credential: StoredCredential,
  id: string,
  decision: "KEEP_VERIFIED" | "USE_CANDIDATE",
) {
  return request<MemoryConflictResolution>(
    credential.baseUrl,
    `/internal/memories/${encodeURIComponent(id)}/resolve-conflict`,
    { method: "POST", body: JSON.stringify({ decision }) },
    credential.accessToken,
  );
}

/** 拒绝错误或不应长期保存的候选，并保留简短审计原因。 */
export function rejectMemoryCandidate(credential: StoredCredential, id: string, reason: string) {
  return request<MemoryCandidate>(credential.baseUrl, `/internal/memories/${id}/reject`, {
    method: "POST",
    body: JSON.stringify({ reason }),
  }, credential.accessToken);
}

/** 永久删除当前用户拥有的一条长期记忆。 */
export function deleteMemoryCandidate(credential: StoredCredential, id: string) {
  return request<void>(credential.baseUrl, `/internal/memories/${id}`, {
    method: "DELETE",
  }, credential.accessToken);
}

/** 创建一条会话设定集规则，供桌面端把人格和回复策略保存到事件中心。 */
export function createConversationProfile(credential: StoredCredential, payload: Record<string, unknown>) {
  return request<ConversationProfile>(credential.baseUrl, "/internal/conversation-profiles", {
    method: "POST",
    body: JSON.stringify(payload),
  }, credential.accessToken);
}

/** 更新当前用户拥有的会话设定，后端会继续执行所有权校验。 */
export function updateConversationProfile(credential: StoredCredential, profileId: string, payload: Record<string, unknown>) {
  return request<ConversationProfile>(credential.baseUrl, `/internal/conversation-profiles/${profileId}`, {
    method: "PUT",
    body: JSON.stringify(payload),
  }, credential.accessToken);
}

/** 在用户明确授权后，同步该设定绑定私聊中的本人历史消息作为训练样本。 */
export function syncConversationHistoryTraining(
  credential: StoredCredential,
  profileId: string,
  count = 100,
) {
  return request<{ importedMessages: number; duplicateMessages: number; skippedMessages: number; personalSkillAvailable: boolean; personalSkillReference: string; eligibleSampleCount: number; confidence: number }>(
    credential.baseUrl,
    `/internal/conversation-profiles/${profileId}/history-training/sync?count=${count}`,
    { method: "POST" },
    credential.accessToken,
  );
}

/** 同步设定集绑定私聊的近期上下文；不等同于授权历史消息用于个人风格训练。 */
export function syncConversationHistoryContext(
  credential: StoredCredential,
  profileId: string,
  count = 100,
) {
  return request<{ importedMessages: number; duplicateMessages: number; skippedMessages: number }>(
    credential.baseUrl,
    `/internal/conversation-profiles/${profileId}/history-context/sync?count=${count}`,
    { method: "POST" },
    credential.accessToken,
  );
}

/** 删除当前用户拥有的会话设定。 */
export function deleteConversationProfile(credential: StoredCredential, profileId: string) {
  return request<void>(credential.baseUrl, `/internal/conversation-profiles/${profileId}`, {
    method: "DELETE",
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

/** 保存设定前验证全部 Skill 是否已安装并适用于目标 Agent route。 */
export function previewSkillResolution(credential: StoredCredential, skillReferences: string[], route: string) {
  return request<SkillResolvePreview>(credential.baseUrl, "/internal/skills/resolve-preview", {
    method: "POST",
    body: JSON.stringify({ skillReferences, route }),
  }, credential.accessToken);
}

/**
 * 解析用户从 QQ Chat Exporter 导出的单文件 JSON，仅返回预览，不会写入历史库。
 */
export function previewQceHistoryImport(
  credential: StoredCredential,
  payload: { exportData: unknown; sourceName: string; chatIdOverride?: string; chatTypeOverride?: string },
) {
  return request<QceImportPreview>(credential.baseUrl, "/internal/history-imports/qce/preview", {
    method: "POST",
    body: JSON.stringify(payload),
  }, credential.accessToken);
}

/**
 * 用户确认预览后才导入 QCE 历史；后端会固定跳过 Agent Runtime 派发。
 */
export function importQceHistory(
  credential: StoredCredential,
  payload: { exportData: unknown; sourceName: string; chatIdOverride?: string; chatTypeOverride?: string },
) {
  return request<QceImportResult>(credential.baseUrl, "/internal/history-imports/qce", {
    method: "POST",
    body: JSON.stringify(payload),
  }, credential.accessToken);
}

/** 将桌面工作台的自然语言命令交给 event-center，由后端安全调用 Python Agent Runtime。 */
export function executeWorkspaceCommand(credential: StoredCredential, prompt: string, requestedRoute: string) {
  return request<WorkspaceCommandResponse>(credential.baseUrl, "/internal/workspace/commands", {
    method: "POST",
    body: JSON.stringify({ prompt, requestedRoute: requestedRoute || null }),
  }, credential.accessToken);
}

/** 查询当前登录用户最近创建的委托任务，用于客户端刷新或重启后恢复任务状态。 */
export function listDelegatedTasks(credential: StoredCredential, limit = 20) {
  const safeLimit = Math.min(100, Math.max(1, Math.trunc(limit)));
  return request<DelegatedTask[]>(
    credential.baseUrl,
    `/internal/workspace/commands/delegated?limit=${safeLimit}`,
    {},
    credential.accessToken,
  );
}

/** 按任务 ID 读取最新详情，打开详情弹窗时使用，避免显示陈旧的本地缓存。 */
export function getDelegatedTask(credential: StoredCredential, taskId: string) {
  return request<DelegatedTask>(
    credential.baseUrl,
    `/internal/workspace/commands/delegated/${encodeURIComponent(taskId)}`,
    {},
    credential.accessToken,
  );
}

/** 用户确认委托草案后把任务推进到待执行状态，接口本身不会直接发送外部消息。 */
export function confirmDelegatedTask(credential: StoredCredential, taskId: string) {
  return request<DelegatedTask>(credential.baseUrl, `/internal/workspace/commands/delegated/${taskId}/confirm`, {
    method: "POST",
  }, credential.accessToken);
}

/** 取消尚未执行的委托任务。 */
export function cancelDelegatedTask(credential: StoredCredential, taskId: string) {
  return request<DelegatedTask>(credential.baseUrl, `/internal/workspace/commands/delegated/${taskId}/cancel`, {
    method: "POST",
  }, credential.accessToken);
}

/** 暂停委托，暂停期间新的会话消息不会再被该任务接管。 */
export function pauseDelegatedTask(credential: StoredCredential, taskId: string) {
  return request<DelegatedTask>(credential.baseUrl, `/internal/workspace/commands/delegated/${taskId}/pause`, {
    method: "POST",
  }, credential.accessToken);
}

/** 继续已暂停的委托，并恢复此前持久化的 LangGraph 状态。 */
export function resumeDelegatedTask(credential: StoredCredential, taskId: string) {
  return request<DelegatedTask>(credential.baseUrl, `/internal/workspace/commands/delegated/${taskId}/resume`, {
    method: "POST",
  }, credential.accessToken);
}

/** 用户主动结束任务，后续消息不再触发该委托。 */
export function completeDelegatedTask(credential: StoredCredential, taskId: string) {
  return request<DelegatedTask>(credential.baseUrl, `/internal/workspace/commands/delegated/${taskId}/complete`, {
    method: "POST",
  }, credential.accessToken);
}

/** 列出当前用户的对话线程，默认不返回已归档项。 */
export function listThreads(credential: StoredCredential, includeArchived = false) {
  const params = new URLSearchParams();
  if (includeArchived) params.set("includeArchived", "true");
  const suffix = params.size ? `?${params.toString()}` : "";
  return request<Thread[]>(credential.baseUrl, `/internal/workspace/threads${suffix}`, {}, credential.accessToken);
}

/** 新建一个空对话线程，标题可选；返回后立即作为当前线程聚焦输入框。 */
export function createThread(credential: StoredCredential, title?: string) {
  return request<Thread>(credential.baseUrl, "/internal/workspace/threads", {
    method: "POST",
    body: JSON.stringify(title ? { title } : {}),
  }, credential.accessToken);
}

/** 更新线程标题、置顶或归档状态；未提供字段由后端保留原值。 */
export function updateThread(credential: StoredCredential, threadId: string, patch: ThreadPatch) {
  return request<Thread>(credential.baseUrl, `/internal/workspace/threads/${encodeURIComponent(threadId)}`, {
    method: "PATCH",
    body: JSON.stringify(patch),
  }, credential.accessToken);
}

/** 分页读取线程内的消息，用于切换线程时恢复历史对话。 */
export function listThreadMessages(credential: StoredCredential, threadId: string, limit = 50) {
  const safeLimit = Math.min(200, Math.max(1, Math.trunc(limit)));
  return request<ThreadMessage[]>(
    credential.baseUrl,
    `/internal/workspace/threads/${encodeURIComponent(threadId)}/messages?limit=${safeLimit}`,
    {},
    credential.accessToken,
  );
}

/**
 * 发送一条用户消息；P1 走同步链路，后端复用命令链路写回 agent 消息后一次性返回。
 * 返回成对的 user/agent 消息与底层命令结果，前端在请求返回前以 pending 气泡兜底。
 */
export function sendThreadMessage(credential: StoredCredential, threadId: string, content: string) {
  return request<ThreadMessageSendResult>(
    credential.baseUrl,
    `/internal/workspace/threads/${encodeURIComponent(threadId)}/messages`,
    { method: "POST", body: JSON.stringify({ content }) },
    credential.accessToken,
  );
}

/** 读取线程内单条消息，用于刷新 agent 消息的最终结果或任务引用。 */
export function getThreadMessage(credential: StoredCredential, threadId: string, messageId: string) {
  return request<ThreadMessage>(
    credential.baseUrl,
    `/internal/workspace/threads/${encodeURIComponent(threadId)}/messages/${encodeURIComponent(messageId)}`,
    {},
    credential.accessToken,
  );
}

/** SSE 阶段事件：stage 为 connected | processing | progress | done | error，其余字段随事件变化。 */
export type ThreadStreamStagePayload = {
  stage: string;
  message?: string;
  agentMessage?: ThreadMessage;
  tasks?: Array<{
    id: string;
    status: string;
    stepKey: string;
    objective: string;
    progressSummary: string;
    workflowId: string;
  }>;
  workflow?: { id: string; status: string; progressSummary: string };
};

/**
 * 订阅单条 agent 消息的执行进度 SSE（P2）。使用 fetch + ReadableStream
 * 解析 text/event-stream，从而支持 Authorization 头（EventSource 无法设置）。
 */
export async function streamThreadMessage(
  credential: StoredCredential,
  threadId: string,
  messageId: string,
  onStage: (payload: ThreadStreamStagePayload) => void,
  signal?: AbortSignal,
): Promise<void> {
  const response = await fetch(
    `${normalizeBaseUrl(credential.baseUrl)}/internal/workspace/threads/${encodeURIComponent(threadId)}/messages/${encodeURIComponent(messageId)}/stream`,
    { headers: { Authorization: `Bearer ${credential.accessToken}` }, signal },
  );
  if (!response.ok) {
    throw new Error(`进度订阅失败：HTTP ${response.status}`);
  }
  const reader = response.body?.getReader();
  if (!reader) return;
  const decoder = new TextDecoder();
  let buffer = "";
  for (;;) {
    const { done, value } = await reader.read();
    if (done) break;
    buffer += decoder.decode(value, { stream: true });
    let delimiter: number;
    while ((delimiter = buffer.indexOf("\n\n")) >= 0) {
      const rawEvent = buffer.slice(0, delimiter);
      buffer = buffer.slice(delimiter + 2);
      const dataLine = rawEvent.split("\n").find((line) => line.startsWith("data:"));
      if (!dataLine) continue;
      try {
        onStage(JSON.parse(dataLine.slice(5).trim()) as ThreadStreamStagePayload);
      } catch {
        // 忽略无法解析的事件，不中断流。
      }
    }
  }
}
