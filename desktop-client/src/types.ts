export type StoredCredential = {
  baseUrl: string;
  accessToken: string;
  userId: string;
  username: string;
  displayName: string;
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
  actionRequired: boolean;
  unreadLikeCount: number | null;
  urgentCount: number | null;
};

export type ConversationProfile = {
  id: string;
  name: string;
  description: string;
  enabled: boolean;
  platform: string;
  chatType: string;
  chatIds: string[];
  personaMode: string;
  replyMode: string;
  notificationMode: string;
  priority: number;
};

export type PlatformConnectionDraft = {
  name: string;
  platform: string;
  connector: string;
  connectorBaseUrl: string;
  credential: string;
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
  description: string;
  sourceType: string;
  reference: string;
  installed: boolean;
};

export type SkillInstallResult = {
  status: string;
  installedReference: string;
  descriptor: SkillDescriptor;
};

export type WorkspaceCommandAgentResult = {
  agent: string;
  status: string;
  replyDraft: string;
  nextActions: string[];
};

export type WorkspaceCommandResponse = {
  commandId: string;
  status: string;
  route: string;
  summary: string;
  finalReply: string;
  needConfirmation: boolean;
  results: WorkspaceCommandAgentResult[];
  error: string;
};
