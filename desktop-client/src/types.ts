export type StoredCredential = {
  baseUrl: string;
  accessToken: string;
  userId: string;
  username: string;
  displayName: string;
};

/** 长期记忆候选的客户端视图；只有 VERIFIED 状态会进入 Agent。 */
export type MemoryCandidate = {
  id: string;
  subject: string;
  predicate: string;
  value: string;
  scopeType: "GLOBAL" | "PLATFORM" | "SCENE" | "CONVERSATION";
  platform: string;
  scene: string;
  chatType: string;
  chatId: string;
  sourceEventIds: string[];
  sourceActorType: string;
  factAuthority: string;
  confidence: number;
  status: "CANDIDATE" | "VERIFIED" | "REJECTED" | "EXPIRED" | "SUPERSEDED";
  rejectionReason: string;
  firstSeenAt: string;
  lastSeenAt: string;
  expiresAt: string | null;
  createdAt: string;
  updatedAt: string;
};

/** 候选记忆的来源证据，只包含来源消息附近的有限聊天窗口。 */
export type MemoryCandidateEvidence = {
  candidateId: string;
  sourceEventIds: string[];
  messages: ConversationMessage[];
  missingEventIds: string[];
};

/** Runtime 单次执行留下的最小审计信息；客户端目前只消费已使用的长期记忆编号。 */
export type ExecutionTrace = {
  executionId: string;
  route: string;
  summary: string;
  writeBackActions: string[];
  verifiedMemoryIds: string[];
};

/** 事件详情的客户端最小视图；服务端返回的其他字段由对应业务页面按需扩展。 */
export type StoredEventDetail = {
  eventId: string;
  executionTrace: ExecutionTrace | null;
};

/** 服务端原子处理记忆冲突后的结果。 */
export type MemoryConflictResolution = {
  candidate: MemoryCandidate;
  supersededMemoryIds: string[];
};

/** 用户手工创建长期记忆候选时使用的最小结构化表单。 */
export type MemoryCandidateDraft = {
  subject: string;
  predicate: string;
  value: string;
  scopeType: "GLOBAL" | "PLATFORM" | "SCENE" | "CONVERSATION";
  platform: string;
  scene: string;
  chatType: string;
  chatId: string;
  expiresAt: string;
};

export type AuthResponse = {
  tokenType: string;
  accessToken: string;
  expiresIn: number;
  userId: string;
  username: string;
  displayName: string;
};

export type PlatformConnection = {
  id: string;
  name: string;
  platform: string;
  connector: string;
  enabled: boolean;
  connected: boolean;
  accountId: string;
  accountName: string;
  health: string;
  message: string;
};

export type ModelProfile = {
  id: string;
  name: string;
  description: string;
  enabled: boolean;
  provider: string;
  baseUrl: string;
  model: string;
  isDefault: boolean;
  hasApiKey: boolean;
  apiKeyMasked: string;
  temperature: number | null;
  maxTokens: number | null;
  supportedRoutes: string[];
  priority: number;
};

export type ModelProfileDraft = {
  name: string;
  description: string;
  enabled: boolean;
  provider: string;
  baseUrl: string;
  apiKey: string;
  model: string;
  temperature: number;
  maxTokens: number;
  supportedRoutes: string[];
  isDefault: boolean;
  priority: number;
};

export type ConversationSummary = {
  platform: string;
  chatType: string;
  chatId: string;
  chatName: string;
  lastSenderName: string;
  lastMessage: string;
  lastMessageTime: string;
  lastDispatchMode: string;
  lastProcessingStatus?: string;
  lastWriteBackStatus?: string;
  actionRequired: boolean;
  unreadLikeCount: number | null;
  urgentCount: number | null;
};

export type ConversationAttachment = {
  fileId: string | null;
  fileName: string | null;
  fileType: string | null;
  url: string | null;
};

export type ConversationMediaAnalysis = {
  attachmentId: string;
  fileName: string;
  fileType: string;
  status: string;
  summary: string;
  extractedText: string;
};

export type ConversationMessage = {
  eventId: string;
  platform: string;
  chatType: string;
  chatId: string;
  chatName: string;
  senderId: string | null;
  senderName: string | null;
  senderRole: string | null;
  senderAvatar: string | null;
  text: string;
  timestamp: string;
  mentions: string[];
  attachments: ConversationAttachment[];
  processed: boolean;
  replied: boolean;
  route: string;
  dispatchMode: string;
  processingStatus: string;
  processingSummary: string;
  writeBackStatus: string;
  needHumanConfirmation: boolean;
  replyDraft: string;
  inboxStatus: string;
  snoozedUntil: string | null;
  messageOrigin: string;
  mediaAnalysis: ConversationMediaAnalysis[];
};

export type ConversationProgressSnapshot = {
  summary: string;
  generatedByModel: boolean;
  generatedAt: string;
  summaryUpdated: boolean;
  latestAgentEventId: string | null;
  messages: ConversationMessage[];
};

export type WorkspaceInboxItem = {
  eventId: string;
  platform: string;
  chatType: string;
  chatId: string;
  chatName: string;
  senderId: string;
  senderName: string;
  text: string;
  timestamp: string;
  route: string;
  processingStatus: string;
  writeBackStatus: string;
  replyDraft: string;
  needHumanConfirmation: boolean;
  actionRequired: boolean;
  inboxStatus: string;
  snoozedUntil: string | null;
  lastAction: string;
  lastActionAt: string | null;
};

export type WorkspaceInbox = {
  generatedAt: string;
  inboxStatusFilter: string;
  totalCount: number;
  newCount: number;
  readCount: number;
  actionRequiredCount: number;
  items: WorkspaceInboxItem[];
};

/** 会话代理任务的持久化状态；任务完成申请在用户审批前仍保持代理在线。 */
export type ConversationProxyTaskState = {
  profileId: string;
  profileName: string;
  platform: string;
  chatType: string;
  chatId: string;
  status: "ACTIVE" | "COMPLETION_REQUESTED" | "COMPLETED" | string;
  completionSummary: string;
  completionReason: string;
  completionEvidence: string[];
  requestedAt: string | null;
  decidedAt: string | null;
  updatedAt: string | null;
};

/** 群管理审批单不包含 Runtime 的一次性令牌，客户端只能按事件提交确认。 */
export type PendingGroupOperation = {
  eventId: string;
  action: string;
  risk: "MEDIUM" | "HIGH" | string;
  confirmationPhrase: string;
  expiresAt: string;
  operation: Record<string, unknown>;
};

export type GroupOperationApprovalResult = {
  status: string;
  action: string;
  risk: string;
  platformResult: Record<string, unknown>;
};

export type WorkspaceScheduleDigest = {
  id: string;
  sourceEventId?: string | null;
  platform?: string | null;
  chatId?: string | null;
  senderId?: string | null;
  title: string;
  startTime: string | null;
  endTime: string | null;
  location: string;
  content: string;
};

/** 桌面端手动创建日程时提交的最小字段集合。 */
export type WorkspaceScheduleDraft = {
  title: string;
  startTime: string;
  endTime: string | null;
  location: string | null;
  content: string | null;
};

/** 日程来源详情，包含原始会话身份和原消息附近的真实上下文。 */
export type WorkspaceScheduleSourceContext = {
  scheduleId: string;
  scheduleTitle: string;
  sourceType: "manual" | "conversation";
  sourceEventId: string;
  platform: string;
  chatType: string;
  chatId: string;
  chatName: string;
  sourceMessageFound: boolean;
  messages: ConversationMessage[];
};

export type WorkspaceBriefing = {
  generatedAt: string;
  lookbackMinutes: number;
  overview: {
    openingLine: string;
    suggestedStart: string;
    importantConversationCount: number;
    pendingTaskCount: number;
    todayScheduleCount: number;
    actionRequiredCount: number;
  };
  importantConversations: Array<{
    platform: string;
    chatType: string;
    chatId: string;
    chatName: string;
    lastSenderName: string;
    lastMessage: string;
    lastMessageTime: string;
    dispatchMode: string;
    highlightReason: string;
    processingStatus: string;
    writeBackStatus: string;
    actionRequired: boolean;
  }>;
  pendingTasks: Array<{
    id: string;
    title: string;
    description: string;
    priority: string;
    status: string;
    dueTime: string | null;
  }>;
  todaySchedules: WorkspaceScheduleDigest[];
  upcomingSchedules?: WorkspaceScheduleDigest[];
  suggestedActions: Array<Record<string, unknown>>;
};

export type ConversationDigestBatch = {
  id: string;
  platform: string;
  chatType: string;
  chatId: string;
  aggregationKey: string;
  sourceEventIds: string[];
  messageCount: number;
  summary: string;
  happened: string;
  actionItems: string;
  nextStep: string;
  periodStartedAt: string | null;
  periodEndedAt: string | null;
  generatedAt: string;
};

/** Conversation Profile 2.0：结构化描述身份、对方、背景、任务、业务规则和资产引用。 */
export type ConversationProfileContext = {
  version: number;
  identity: {
    representedPerson: string;
    role: string;
    speakingStyle: string;
    forbiddenExpressions: string[];
  };
  counterparty: {
    name: string;
    identity: string;
    relationship: string;
    preferredAddress: string;
    knownFacts: string[];
    trustLevel: "UNKNOWN" | "LOW" | "MEDIUM" | "HIGH";
    communicationPreference: string;
  };
  background: {
    origin: string;
    previousEvents: string;
    currentProgress: string;
  };
  task: {
    objective: string;
    successCriteria: string[];
    deadline: string;
    prohibitedActions: string[];
  };
  businessRules: {
    pricingPolicy: string;
    minimumPrice: string;
    refundPolicy: string;
    deliveryConditions: string;
    hardConstraints: string[];
  };
  memoryPolicy: {
    extractionEnabled: boolean;
  };
  assets: Array<{
    assetId: string;
    type: string;
    name: string;
    description: string;
    usageCondition: string;
  }>;
};

/** 单个认知字段同时保存模型结论、置信度和用户锁定状态，避免低可信推断伪装成事实。 */
export type ConversationCognitionField = {
  value: string;
  confidence: number;
  source: string;
  lockedByUser: boolean;
};

/** 会话认知卡由历史消息增量生成，作为 Prompt 的有证据上下文而不是用户必填表单。 */
export type ConversationCognitionCard = {
  id: string;
  platform: string;
  chatType: string;
  chatId: string;
  version: number;
  relationship: ConversationCognitionField;
  preferredAddress: ConversationCognitionField;
  counterpartyTraits: ConversationCognitionField;
  ownerExpressionHabits: ConversationCognitionField;
  counterpartyExpressionHabits: ConversationCognitionField;
  backgroundSummary: ConversationCognitionField;
  currentProgress: ConversationCognitionField;
  knownFacts: string[];
  recentTopics: string[];
  openQuestions: string[];
  sourceEventIds: string[];
  sourceMessageCount: number;
  status: string;
  analyzedAt: string | null;
  createdAt: string;
  updatedAt: string;
};

/**
 * 安全资产的桌面端元数据。
 * 服务端不会在普通用户接口中返回 content 或 ciphertext，因此该类型也刻意不声明敏感正文。
 */
export type SecureAsset = {
  id: string;
  name: string;
  type: string;
  description: string;
  contentType: string;
  usagePolicy: "REUSABLE" | "SINGLE_USE";
  remainingUses: number | null;
  enabled: boolean;
  contentConfigured: boolean;
  createdAt: string;
  updatedAt: string;
  lastUsedAt: string | null;
};

/** 创建或更新安全资产时使用的表单数据；编辑时 content 留空表示保留原正文。 */
export type SecureAssetDraft = {
  name: string;
  type: string;
  description: string;
  contentType: string;
  content: string | null;
  usagePolicy: "REUSABLE" | "SINGLE_USE";
  remainingUses: number | null;
  enabled: boolean;
};

export type ConversationProfile = {
  id: string;
  name: string;
  description: string;
  enabled: boolean;
  platform: string;
  chatType: string;
  chatIds: string[];
  targetUserIds: string[];
  supportedRoutes: string[];
  triggerMode: string;
  triggerKeywords: string[];
  personaMode: string;
  systemPrompt: string;
  skillReference: string;
  skillReferences: string[];
  modelProfileId: string;
  preferredRoute: string;
  replyMode: string;
  allowedTools: string[];
  requireHumanConfirmation: boolean;
    notificationMode: string;
    notificationKeywords: string[];
    digestWindowSeconds: number | null;
    digestMaxMessages: number | null;
    includeUrgentInDigest: boolean;
  priority: number;
  maxReplyChars: number;
    splitLongReply: boolean;
    splitReplyChancePercent: number;
    privateHistoryEnabled: boolean;
    historyMaxMessages: number;
    historyMaxChars: number;
      historyTrainingEnabled: boolean;
      reviewMode: string;
      knowledgeBaseSources: string[];
      profileContext: ConversationProfileContext;
};

export type PlatformConnectionDraft = {
  name: string;
  platform: string;
  connector: string;
  connectorBaseUrl: string;
  credential: string;
};

/** NapCat 扫码登录的稳定状态；客户端不接触 WebUI Token 或内部响应格式。 */
export type NapcatQrLoginState = {
  state: "RESTORING" | "WAITING_SCAN" | "CONNECTED" | "CONFIG_FAILED" | "SETUP_REQUIRED" | "NAPCAT_OFFLINE" | "OFFLINE" | "QR_UNAVAILABLE" | "DISABLED" | "ERROR";
  qrCodeUrl: string;
  message: string;
  accountId: string;
  accountName: string;
  onebotConfigured: boolean;
};

export type QqContact = {
  id: string;
  name: string;
  type: "private" | "group";
  remark: string;
};

export type SkillDescriptor = {
  id: string;
  name: string;
  version: string;
  type: string;
  description: string;
  sourceType: string;
  reference: string;
  applicableRoutes: string[];
  promptFragments: {
    system: string;
  };
  toolPolicy: {
    allow: string[];
  };
  modelHints: {
    temperature: number | null;
    maxTokens: number | null;
  };
  installed: boolean;
  location: string;
};

export type SkillInstallResult = {
  status: string;
  installedReference: string;
  descriptor: SkillDescriptor;
};

/** 保存会话设定前的 Skill 解析结果，用于明确区分已生效和未解析引用。 */
export type SkillResolvePreview = {
  resolvedSkills: SkillDescriptor[];
  unresolvedSkillReferences: string[];
};

/** QCE 单文件 JSON 在写库前返回的会话、时间和媒体统计。 */
export type QceImportPreview = {
  chatName: string;
  detectedChatType: string;
  detectedChatId: string;
  selfId: string;
  requiresChatIdMapping: boolean;
  totalMessages: number;
  textMessages: number;
  attachmentMessages: number;
  imageAttachments: number;
  videoAttachments: number;
  audioAttachments: number;
  fileAttachments: number;
  startedAt: string | null;
  endedAt: string | null;
  samples: Array<{
    messageId: string;
    senderName: string;
    text: string;
    timestamp: string;
    attachmentCount: number;
  }>;
  warnings: string[];
};

/** QCE 历史导入完成后展示的去重和附件统计。 */
export type QceImportResult = {
  chatId: string;
  chatType: string;
  importedCount: number;
  duplicateCount: number;
  attachmentCount: number;
  message: string;
};

export type WorkspaceCommandAgentResult = {
  agent: string;
  status: string;
  replyDraft: string;
  nextActions: string[];
};

/** 工作台自然语言创建的持续委托任务。 */
export type DelegatedTask = {
  id: string;
  taskType: "REPLY_ONCE" | "CONVERSATION_GOAL" | string;
  status: string;
  originalCommand: string;
  targetQuery: string;
  platform: string;
  chatType: string;
  chatId: string;
  targetName: string;
  objective: string;
  successCriteria: string;
  deadlineText: string;
  confidence: number;
  clarificationQuestion: string;
  requiresConfirmation: boolean;
  executionMode: string;
  progressSummary: string;
  stateJson: string;
  lastEventId: string;
  startedAt: string | null;
  completedAt: string | null;
  completionReport: string;
  createdAt: string;
  updatedAt: string;
};

/** 用户可以从委托任务控制台触发的生命周期操作。 */
export type DelegatedTaskControlAction = "pause" | "resume" | "cancel" | "complete";

export type WorkspaceCommandResponse = {
  commandId: string;
  status: string;
  route: string;
  summary: string;
  finalReply: string;
  needConfirmation: boolean;
  results: WorkspaceCommandAgentResult[];
  delegatedTask: DelegatedTask | null;
  error: string;
};
