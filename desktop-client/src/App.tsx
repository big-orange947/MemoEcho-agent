import { ChangeEvent, FormEvent, useEffect, useRef, useState } from "react";
import {
  ArrowClockwise,
  Brain,
  Bell,
  CalendarDots,
  ChatCircleDots,
  ChatText,
  Cpu,
  DotsThree,
  DownloadSimple,
  CheckCircle,
  ArrowSquareOut,
  FileText,
  GithubLogo,
  HardDrives,
  House,
  Key,
  MapPinLine,
  MagnifyingGlass,
  PaperPlaneRight,
  PencilSimple,
  PlugsConnected,
  Plus,
  ShieldCheck,
  SignOut,
  SlidersHorizontal,
  Sparkle,
  Trash,
  UsersThree,
} from "@phosphor-icons/react";
import {
  checkConnection,
  checkHealth,
  approvePendingGroupOperation,
  confirmInboxDraft,
  createConnection,
  createConversationProfile,
  createModelProfile,
  createMemoryCandidate,
  createSecureAsset,
  createWorkspaceSchedule,
  decideConversationTaskCompletion,
  deleteModelProfile,
  deleteMemoryCandidate,
  deleteSecureAsset,
  deleteConversationProfile,
  deleteWorkspaceSchedule,
  executeWorkspaceCommand,
  listDelegatedTasks,
  getDelegatedTask,
  confirmDelegatedTask,
  cancelDelegatedTask,
  pauseDelegatedTask,
  resumeDelegatedTask,
  completeDelegatedTask,
  getConversationProgress,
  getMemoryCandidateEvidence,
  getStoredEventDetail,
  getPendingGroupOperation,
  getNapcatQrLoginStatus,
  getWorkspaceBriefing,
  getWorkspaceScheduleSourceContext,
  installGithubSkill,
  importQceHistory,
  listConnections,
  listConversationDigests,
  listConversationProfiles,
  listPendingConversationTaskCompletions,
  listConversations,
  listModelProfiles,
  listMemoryCandidates,
  listSecureAssets,
  listSkills,
  listWorkspaceInbox,
  login,
  previewQceHistoryImport,
  previewSkillResolution,
  refreshNapcatQrLogin,
  refreshConversationCognition,
  register,
  rejectMemoryCandidate,
  resolveMemoryConflict,
  searchQqContacts,
  startNapcatQrLogin,
  syncConversationHistoryContext,
  syncConversationHistoryTraining,
  updateModelProfile,
  updateMemoryCandidate,
  updateSecureAsset,
  updateConversationProfile,
  updateConversationAgentState,
  verifyMemoryCandidate,
} from "./api/client";
import { loadCredential, removeCredential, saveCredential } from "./api/secure-store";
import {
  getNapcatRuntimeInstallProgress,
  getNapcatRuntimeStatus,
  rememberNapcatAccount,
  startNapcatRuntimeInstall,
  startNapcatRuntime,
  type NapcatInstallProgress,
  type NapcatRuntimeStatus,
} from "./api/napcat-runtime";
import { ConversationContextDialog } from "./components/ConversationContextDialog";
import { MemoryAuditDialog } from "./components/MemoryAuditDialog";
import { QceHistoryImporter } from "./components/QceHistoryImporter";
import { ScheduleEditorDialog } from "./components/ScheduleEditorDialog";
import { SecureAssetManagerDialog, SecureAssetReferenceEditor } from "./components/SecureAssetManager";
import type {
  ConversationProfile,
  ConversationProfileContext,
  ConversationDigestBatch,
  ConversationProgressSnapshot,
  ConversationProxyTaskState,
  ConversationSummary,
  ModelProfile,
  ModelProfileDraft,
  MemoryCandidate,
  MemoryCandidateEvidence,
  MemoryCandidateDraft,
  NapcatQrLoginState,
  PendingGroupOperation,
  PlatformConnection,
  PlatformConnectionDraft,
  QceImportPreview,
  QqContact,
  SkillDescriptor,
  SecureAsset,
  SecureAssetDraft,
  StoredCredential,
  StoredEventDetail,
  WorkspaceCommandResponse,
  DelegatedTask,
  DelegatedTaskControlAction,
  WorkspaceBriefing,
  WorkspaceScheduleDigest,
  WorkspaceScheduleDraft,
  WorkspaceScheduleSourceContext,
  WorkspaceInbox,
  WorkspaceInboxItem,
} from "./types";
import { WorkspaceConsole } from "./components/workspace/WorkspaceConsole";

const DEFAULT_BASE_URL = "http://127.0.0.1:8093";
const MONITOR_PROFILE_MARKER = "__MESSAGE_MONITORING__";
const EMPTY_CONNECTION: PlatformConnectionDraft = {
  name: "本地 QQ / NapCat",
  platform: "qq",
  connector: "qq-napcat",
  connectorBaseUrl: "http://127.0.0.1:8091",
  credential: "",
};

type View = "console" | "dashboard" | "messages" | "monitoring" | "profiles" | "memories" | "models" | "connections";

const VIEW_LABELS: Record<View, string> = {
  console: "主控台",
  dashboard: "今日脉搏",
  messages: "消息空间",
  monitoring: "消息监控",
  profiles: "设定集",
  memories: "长期记忆",
  models: "模型配置",
  connections: "连接管理",
};

const EMPTY_MEMORY: MemoryCandidateDraft = {
  subject: "",
  predicate: "",
  value: "",
  scopeType: "GLOBAL",
  platform: "",
  scene: "",
  chatType: "",
  chatId: "",
  expiresAt: "",
};

const MODEL_ROUTES = [
  "social_reply",
  "chat_summary",
  "task_plan",
  "schedule_extract",
  "file_analysis",
  "vision_analysis",
  "message_dispatch",
  "group_ops",
];

const EMPTY_MODEL: ModelProfileDraft = {
  name: "",
  description: "",
  enabled: true,
  provider: "OPENAI_COMPATIBLE",
  baseUrl: "https://api.openai.com/v1",
  apiKey: "",
  model: "",
  temperature: 0.4,
  maxTokens: 2048,
  supportedRoutes: [],
  isDefault: false,
  priority: 0,
};

type ModelPreset = ModelProfileDraft & {
  vendor: "DeepSeek" | "Qwen" | "GLM" | "Kimi" | "MiniMax";
  level: string;
  note: string;
  capabilities?: Array<"TEXT" | "VISION" | "AGENT">;
  recommendedRoutes?: string[];
};

const LEGACY_MODEL_PRESETS: ModelPreset[] = [
  { ...EMPTY_MODEL, vendor: "DeepSeek", name: "DeepSeek Chat", model: "deepseek-chat", baseUrl: "https://api.deepseek.com", level: "High", note: "通用推理与任务规划" },
  { ...EMPTY_MODEL, vendor: "Qwen", name: "Qwen Plus", model: "qwen-plus", baseUrl: "https://dashscope.aliyuncs.com/compatible-mode/v1", level: "High", note: "中文理解与工具调用" },
  { ...EMPTY_MODEL, vendor: "GLM", name: "GLM-4 Flash", model: "glm-4-flash", baseUrl: "https://open.bigmodel.cn/api/paas/v4", level: "Medium", note: "轻量对话与摘要" },
  { ...EMPTY_MODEL, vendor: "Kimi", name: "Moonshot V1", model: "moonshot-v1-8k", baseUrl: "https://api.moonshot.cn/v1", level: "Medium", note: "长文本与文件理解" },
];

/** 当前厂商官方文档中可直接通过 OpenAI Compatible 接口接入的推荐模型目录。 */
const CURRENT_MODEL_PRESETS: ModelPreset[] = [
  { ...EMPTY_MODEL, vendor: "DeepSeek", name: "DeepSeek V4 Flash", model: "deepseek-v4-flash", baseUrl: "https://api.deepseek.com", level: "Fast", note: "低延迟日常对话、摘要与分流", capabilities: ["TEXT"], recommendedRoutes: ["chat_summary", "message_dispatch"] },
  { ...EMPTY_MODEL, vendor: "DeepSeek", name: "DeepSeek V4 Pro", model: "deepseek-v4-pro", baseUrl: "https://api.deepseek.com", level: "High", note: "复杂回复、规划和审查，不支持图片输入", capabilities: ["TEXT", "AGENT"], recommendedRoutes: ["social_reply", "task_plan", "schedule_extract"] },
  { ...EMPTY_MODEL, vendor: "Qwen", name: "Qwen3-VL Flash", model: "qwen3-vl-flash", baseUrl: "https://dashscope.aliyuncs.com/compatible-mode/v1", level: "Value", note: "低成本看图、截图和图片文字识别", capabilities: ["TEXT", "VISION"], recommendedRoutes: ["vision_analysis", "file_analysis"] },
  { ...EMPTY_MODEL, vendor: "Qwen", name: "Qwen3-VL Plus", model: "qwen3-vl-plus", baseUrl: "https://dashscope.aliyuncs.com/compatible-mode/v1", level: "High", note: "复杂图片、文档截图和多模态理解", capabilities: ["TEXT", "VISION", "AGENT"], recommendedRoutes: ["vision_analysis", "file_analysis"] },
  { ...EMPTY_MODEL, vendor: "GLM", name: "GLM-5", model: "glm-5", baseUrl: "https://open.bigmodel.cn/api/paas/v4", level: "High", note: "长程 Agent、工程任务与工具调用", capabilities: ["TEXT", "AGENT"], recommendedRoutes: ["task_plan", "group_ops"] },
  { ...EMPTY_MODEL, vendor: "Kimi", name: "Kimi K2.6", model: "kimi-k2.6", baseUrl: "https://api.moonshot.cn/v1", level: "High", note: "长上下文、视觉理解与 Agent 任务", capabilities: ["TEXT", "VISION", "AGENT"], recommendedRoutes: ["vision_analysis", "social_reply", "file_analysis"] },
  { ...EMPTY_MODEL, vendor: "MiniMax", name: "MiniMax M2.7", model: "MiniMax-M2.7", baseUrl: "https://api.minimaxi.com/v1", level: "Agent", note: "长上下文、工具调用与执行型工作流", capabilities: ["TEXT", "AGENT"], recommendedRoutes: ["task_plan", "group_ops", "message_dispatch"] },
];

type ConversationProfileDraft = {
  name: string;
  chatType: string;
  contactIds: string[];
  systemPrompt: string;
  replyMode: string;
  skillMode: "prompt" | "personal" | "local" | "github";
  skillReference: string;
  skillReferences: string[];
  githubReference: string;
  modelProfileId: string;
  maxReplyChars: number;
    splitLongReply: boolean;
    splitReplyChancePercent: number;
    privateHistoryEnabled: boolean;
    historyMaxMessages: number;
    historyMaxChars: number;
    historyTrainingEnabled: boolean;
    notificationMode: string;
    notificationKeywords: string;
    digestWindowSeconds: number;
    digestMaxMessages: number;
    includeUrgentInDigest: boolean;
    reviewMode: string;
    knowledgeBaseSources: string;
    groupManagementEnabled: boolean;
    profileContext: ConversationProfileContext;
};

const EMPTY_PROFILE_CONTEXT: ConversationProfileContext = {
  version: 2,
  identity: { representedPerson: "本人", role: "本人", speakingStyle: "", forbiddenExpressions: [] },
  counterparty: {
    name: "", identity: "", relationship: "", preferredAddress: "", knownFacts: [],
    trustLevel: "UNKNOWN", communicationPreference: "",
  },
  background: { origin: "", previousEvents: "", currentProgress: "" },
  task: { objective: "", successCriteria: [], deadline: "", prohibitedActions: [] },
  businessRules: {
    pricingPolicy: "", minimumPrice: "", refundPolicy: "", deliveryConditions: "", hardConstraints: [],
  },
  memoryPolicy: { extractionEnabled: false },
  assets: [],
};

const EMPTY_PROFILE: ConversationProfileDraft = {
  name: "", chatType: "private", contactIds: [], systemPrompt: "", replyMode: "DRAFT_ONLY",
  skillMode: "prompt", skillReference: "", skillReferences: [], githubReference: "",
   modelProfileId: "", maxReplyChars: 24, splitLongReply: true, splitReplyChancePercent: 33,
   privateHistoryEnabled: false, historyMaxMessages: 12, historyMaxChars: 2000,
    historyTrainingEnabled: false,
    notificationMode: "AUTO", notificationKeywords: "", digestWindowSeconds: 1800,
    digestMaxMessages: 20, includeUrgentInDigest: false,
    reviewMode: "STRICT_HANDOFF",
    knowledgeBaseSources: "",
    groupManagementEnabled: false,
    profileContext: EMPTY_PROFILE_CONTEXT,
};

/** 将登录响应整理为 Windows 凭据管理器保存的最小会话数据。 */
function toCredential(baseUrl: string, response: Awaited<ReturnType<typeof login>>): StoredCredential {
  return {
    baseUrl: baseUrl.trim(), accessToken: response.accessToken, userId: response.userId,
    username: response.username, displayName: response.displayName,
  };
}

/** 格式化后端 ISO 时间，接口缺少时间时显示占位文本。 */
function formatTime(value: string) {
  return value ? new Date(value).toLocaleString("zh-CN", { hour: "2-digit", minute: "2-digit", month: "short", day: "numeric" }) : "暂无时间";
}

/** 把后端 ISO 时间转换为 datetime-local 控件需要的本地时间文本。 */
function toDatetimeLocal(value: string | null) {
  if (!value) return "";
  const date = new Date(value);
  const offset = date.getTimezoneOffset() * 60_000;
  return new Date(date.getTime() - offset).toISOString().slice(0, 16);
}

/** 等待 NapCat 使用本机 QQ 缓存完成快速登录；超时只返回当前状态，不会擅自生成新二维码。 */
async function waitForNapcatSessionRestore(
  credential: StoredCredential,
  timeoutMs = 20_000,
): Promise<NapcatQrLoginState> {
  const deadline = Date.now() + timeoutMs;
  let state = await getNapcatQrLoginStatus(credential);
  while (state.state === "RESTORING" && Date.now() < deadline) {
    await new Promise((resolve) => window.setTimeout(resolve, 1_000));
    state = await getNapcatQrLoginStatus(credential);
  }
  return state;
}

/** 渲染登录、注册、消息、设定集和连接管理页面的桌面客户端根组件。 */
export function App() {
  const [credential, setCredential] = useState<StoredCredential | null>(null);
  const [baseUrl, setBaseUrl] = useState(DEFAULT_BASE_URL);
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [displayName, setDisplayName] = useState("");
  const [registerMode, setRegisterMode] = useState(false);
  const [activeView, setActiveView] = useState<View>("console");
  const [connections, setConnections] = useState<PlatformConnection[]>([]);
  const [modelProfiles, setModelProfiles] = useState<ModelProfile[]>([]);
  const [modelDraft, setModelDraft] = useState<ModelProfileDraft>(EMPTY_MODEL);
  const [editingModelId, setEditingModelId] = useState("");
  const [modelEditorOpen, setModelEditorOpen] = useState(false);
  const [conversations, setConversations] = useState<ConversationSummary[]>([]);
  const [workspaceInbox, setWorkspaceInbox] = useState<WorkspaceInbox | null>(null);
  const [pendingTaskCompletions, setPendingTaskCompletions] = useState<ConversationProxyTaskState[]>([]);
  const [workspaceBriefing, setWorkspaceBriefing] = useState<WorkspaceBriefing | null>(null);
  const [seenHandoffIds, setSeenHandoffIds] = useState<string[]>([]);
  const [conversationDigests, setConversationDigests] = useState<ConversationDigestBatch[]>([]);
  const [conversationProfiles, setConversationProfiles] = useState<ConversationProfile[]>([]);
  const [memoryCandidates, setMemoryCandidates] = useState<MemoryCandidate[]>([]);
  const [memoryDraft, setMemoryDraft] = useState<MemoryCandidateDraft>(EMPTY_MEMORY);
  const [memoryEditorOpen, setMemoryEditorOpen] = useState(false);
  const [editingMemoryId, setEditingMemoryId] = useState("");
  const [memoryEvidenceOpen, setMemoryEvidenceOpen] = useState(false);
  const [memoryEvidenceItem, setMemoryEvidenceItem] = useState<MemoryCandidate | null>(null);
  const [memoryEvidence, setMemoryEvidence] = useState<MemoryCandidateEvidence | null>(null);
  const [memoryEvidenceLoading, setMemoryEvidenceLoading] = useState(false);
  const [memoryEvidenceError, setMemoryEvidenceError] = useState("");
  const [connectionDraft, setConnectionDraft] = useState<PlatformConnectionDraft>(EMPTY_CONNECTION);
  const [qrLogin, setQrLogin] = useState<NapcatQrLoginState | null>(null);
  const [qrLoginOpen, setQrLoginOpen] = useState(false);
  const [qrLoginBusy, setQrLoginBusy] = useState(false);
  const [napcatRuntime, setNapcatRuntime] = useState<NapcatRuntimeStatus | null>(null);
  const [napcatInstallProgress, setNapcatInstallProgress] = useState<NapcatInstallProgress | null>(null);
  const [napcatLicenseAccepted, setNapcatLicenseAccepted] = useState(false);
  const napcatRuntimeRestoreRef = useRef("");
  const [profileDraft, setProfileDraft] = useState<ConversationProfileDraft>(EMPTY_PROFILE);
  const [profileEditorOpen, setProfileEditorOpen] = useState(false);
  const [editingProfileId, setEditingProfileId] = useState("");
  const [qqContacts, setQqContacts] = useState<QqContact[]>([]);
  const [qqContactsError, setQqContactsError] = useState("");
  const [skills, setSkills] = useState<SkillDescriptor[]>([]);
  const [secureAssets, setSecureAssets] = useState<SecureAsset[]>([]);
  const [contactKeyword, setContactKeyword] = useState("");
  // QCE 文件仅暂存在当前页面内存，刷新或退出客户端不会留下聊天原文副本。
  const [qceExportData, setQceExportData] = useState<unknown | null>(null);
  const [qceFileName, setQceFileName] = useState("");
  const [qcePreview, setQcePreview] = useState<QceImportPreview | null>(null);
  const [qceChatIdOverride, setQceChatIdOverride] = useState("");
  const [qceChatTypeOverride, setQceChatTypeOverride] = useState("");

  /** 页面切换时关闭其他页面遗留的编辑器，避免设定集表单串到消息监控页面。 */
  useEffect(() => {
    if (activeView !== "profiles") {
      setProfileEditorOpen(false);
      setEditingProfileId("");
      setProfileDraft(EMPTY_PROFILE);
    }
  }, [activeView]);

  /** 离开长期记忆页面时清理编辑器；来源证据在消息空间也会复用，因此单独管理。 */
  useEffect(() => {
    if (activeView !== "memories") {
      setMemoryEditorOpen(false);
      setEditingMemoryId("");
      setMemoryDraft(EMPTY_MEMORY);
    }
    if (activeView !== "memories" && activeView !== "messages") {
      setMemoryEvidenceOpen(false);
      setMemoryEvidenceItem(null);
      setMemoryEvidence(null);
      setMemoryEvidenceError("");
    }
  }, [activeView]);
  const [status, setStatus] = useState("正在读取本地登录状态…");
  const [busy, setBusy] = useState(false);

  /** 启动时从 Windows 凭据管理器恢复上次登录状态。 */
  useEffect(() => {
    loadCredential().then((saved) => {
      if (saved) {
        setCredential(saved);
        setBaseUrl(saved.baseUrl);
      }
      setStatus(saved ? "已恢复本地登录状态" : "请登录你的 Memo Echo 本地服务");
    }).catch(() => setStatus("无法读取 Windows 凭据管理器"));
  }, []);

  /** 在已登录或切换页面时拉取当前页面需要的数据。 */
  useEffect(() => {
    if (credential) void loadView(activeView, credential);
  }, [credential, activeView]);

  /**
   * 客户端重新打开后自动恢复已经安装的托管 NapCat。
   * 恢复成功后立即刷新连接健康状态和联系人，避免数据库里的旧“已连接”状态掩盖运行时已经退出。
   */
  useEffect(() => {
    if (!credential) {
      napcatRuntimeRestoreRef.current = "";
      return;
    }
    const restoreKey = `${credential.baseUrl}:${credential.userId}`;
    if (napcatRuntimeRestoreRef.current === restoreKey) return;
    napcatRuntimeRestoreRef.current = restoreKey;
    let cancelled = false;

    const restoreRuntime = async () => {
      try {
        // 连接记录中保存的只是 QQ 号和本地端口，不包含 QQ 密码；QQ 号用于选择 NapCat 已缓存的登录会话。
        let nextConnections = await listConnections(credential);
        const qqConnection = nextConnections.find((item) => item.platform === "qq" && item.enabled);
        const accountId = qqConnection?.accountId || undefined;
        let runtime = await getNapcatRuntimeStatus();
        if (cancelled) return;
        setNapcatRuntime(runtime);
        if (runtime.installed && !runtime.ready) {
          setStatus("正在恢复本机 QQ 连接组件…");
          runtime = await startNapcatRuntime(accountId);
          if (cancelled) return;
          setNapcatRuntime(runtime);
        }
        // 未安装时停留在连接管理引导页，不在后台静默下载第三方组件。
        if (!runtime.ready) return;

        setStatus(accountId ? `正在恢复 QQ ${accountId} 的本地登录状态…` : "正在检查本机 QQ 登录状态…");
        const loginState = await waitForNapcatSessionRestore(credential);
        if (cancelled) return;
        if (loginState.state !== "CONNECTED") {
          setQrLogin(loginState);
          setStatus(accountId
            ? "QQ 本地登录缓存已失效，请在连接管理中重新扫码"
            : "尚未找到可复用的 QQ 登录状态，请在连接管理中扫码");
          return;
        }
        if (loginState.accountId) {
          await rememberNapcatAccount(loginState.accountId).catch(() => undefined);
        }
        if (qqConnection) {
          await checkConnection(credential, qqConnection.id);
          nextConnections = await listConnections(credential);
        }
        const contacts = await searchQqContacts(credential, "");
        if (cancelled) return;
        setConnections(nextConnections);
        setQqContacts(contacts);
        setQqContactsError("");
        setStatus(`QQ 连接已恢复，已读取 ${contacts.length} 个会话`);
      } catch (error) {
        if (!cancelled) {
          const message = error instanceof Error ? error.message : "NapCat 自动恢复失败";
          setQqContactsError(message);
          setStatus(`QQ 连接未恢复：${message}`);
        }
      }
    };

    void restoreRuntime();
    return () => { cancelled = true; };
  }, [credential]);

  /** 即使用户停留在其他页面，也定时刷新待接管数量，确保侧栏红点不会依赖手动进入消息空间。 */
  useEffect(() => {
    if (!credential) return;
    let cancelled = false;
    const refreshInbox = async () => {
      try {
        const nextInbox = await listWorkspaceInbox(credential);
        if (!cancelled) setWorkspaceInbox(nextInbox);
      } catch {
        // 红点刷新失败不覆盖页面已有数据，也不打断用户正在编辑的设定集。
      }
    };
    void refreshInbox();
    const timer = window.setInterval(() => void refreshInbox(), 30_000);
    return () => { cancelled = true; window.clearInterval(timer); };
  }, [credential]);

  /** 每个本地用户独立保存已经查看过的接管事件，避免红点在看完后仍长期常亮。 */
  useEffect(() => {
    if (!credential) {
      setSeenHandoffIds([]);
      return;
    }
    try {
      const saved = window.localStorage.getItem(`memo-echo:seen-handoffs:${credential.userId}`);
      setSeenHandoffIds(saved ? JSON.parse(saved) : []);
    } catch {
      setSeenHandoffIds([]);
    }
  }, [credential]);

  /** 只有用户真正打开“等待接管”分页后才清除红点，避免进入消息空间就误判为已查看。 */
  function markHandoffsViewed() {
    if (!credential) return;
    const inboxIds = (workspaceInbox?.items || [])
      .filter((item) => item.needHumanConfirmation)
      .map((item) => item.eventId);
    const taskIds = pendingTaskCompletions.map((item) => buildTaskCompletionSeenId(item));
    const currentIds = [...inboxIds, ...taskIds];
    if (currentIds.every((id) => seenHandoffIds.includes(id))) return;
    const nextIds = Array.from(new Set([...seenHandoffIds, ...currentIds])).slice(-500);
    setSeenHandoffIds(nextIds);
    window.localStorage.setItem(`memo-echo:seen-handoffs:${credential.userId}`, JSON.stringify(nextIds));
  }

  /** 根据页面类型读取后端数据，并统一更新加载状态和可读错误消息。 */
  async function loadView(view: View, currentCredential: StoredCredential) {
    setBusy(true);
    try {
      // 导航红点需要在任意页面都知道待审核数量；失败时不阻断当前页面主体数据。
      const backgroundMemoryRequest = view === "memories"
        ? null
        : listMemoryCandidates(currentCredential).catch(() => null);
      const backgroundTaskCompletionRequest = view === "messages"
        ? null
        : listPendingConversationTaskCompletions(currentCredential).catch(() => null);
      if (view === "messages") {
        const [nextConversations, nextInbox, nextConnections, nextTaskCompletions] = await Promise.all([
          listConversations(currentCredential),
          listWorkspaceInbox(currentCredential),
          listConnections(currentCredential),
          listPendingConversationTaskCompletions(currentCredential),
        ]);
        setConversations(nextConversations);
        setWorkspaceInbox(nextInbox);
        setConnections(nextConnections);
        setPendingTaskCompletions(nextTaskCompletions);
        const senderId = nextConnections.find((item) => item.platform === "qq" && item.accountId)?.accountId
          || currentCredential.userId;
        const [digestResult, briefingResult] = await Promise.allSettled([
          listConversationDigests(currentCredential),
          getWorkspaceBriefing(currentCredential, senderId, currentCredential.displayName || currentCredential.username),
        ]);
        setConversationDigests(digestResult.status === "fulfilled" ? digestResult.value : []);
        setWorkspaceBriefing(briefingResult.status === "fulfilled" ? briefingResult.value : null);
        setStatus(digestResult.status === "fulfilled" ? "服务已连接，数据已刷新" : "接管与代理进度已刷新，摘要暂不可用");
      } else if (view === "profiles") {
        const [nextProfiles, nextModels, nextAssets] = await Promise.all([
          listConversationProfiles(currentCredential),
          listModelProfiles(currentCredential),
          listSecureAssets(currentCredential),
        ]);
        setConversationProfiles(nextProfiles);
        setModelProfiles(nextModels);
        setSecureAssets(nextAssets);
      } else if (view === "monitoring") {
        setQqContactsError("");
        const [nextProfiles, contacts] = await Promise.all([
          listConversationProfiles(currentCredential), searchQqContacts(currentCredential, ""),
        ]);
        setConversationProfiles(nextProfiles);
        setQqContacts(contacts);
      } else if (view === "models") {
        setModelProfiles(await listModelProfiles(currentCredential));
      } else if (view === "memories") {
        setMemoryCandidates(await listMemoryCandidates(currentCredential));
      } else {
        const [nextConnections, nextModels] = await Promise.all([
          listConnections(currentCredential), listModelProfiles(currentCredential),
        ]);
        setConnections(nextConnections);
        setModelProfiles(nextModels);
      }
      if (backgroundMemoryRequest) {
        const nextMemories = await backgroundMemoryRequest;
        if (nextMemories) setMemoryCandidates(nextMemories);
      }
      if (backgroundTaskCompletionRequest) {
        const nextTaskCompletions = await backgroundTaskCompletionRequest;
        if (nextTaskCompletions) setPendingTaskCompletions(nextTaskCompletions);
      }
      if (view !== "messages") setStatus("服务已连接，数据已刷新");
    } catch (error) {
      const message = error instanceof Error ? error.message : "读取客户端数据失败";
      if (view === "monitoring") setQqContactsError(message);
      setStatus(message);
    } finally {
      setBusy(false);
    }
  }

  const workspaceStreamKey = connections
    .filter((connection) => connection.platform && connection.accountId)
    .map((connection) => `${connection.platform}:${connection.accountId}`)
    .sort()
    .join("|");

  /** 订阅已连接账号的工作台事件；仅账号集合变化时重建，页面切换不会反复断开 SSE。 */
  useEffect(() => {
    if (!credential || connections.length === 0) return;
    const streams = connections
      .filter((connection) => connection.platform && connection.accountId)
      .map((connection) => {
        const query = new URLSearchParams({ platform: connection.platform, accountId: connection.accountId });
        const stream = new EventSource(`${credential.baseUrl}/internal/workspace/stream?${query.toString()}`);
        const refreshMessages = () => {
          setStatus("收到新的工作台事件，正在刷新消息空间…");
          void listWorkspaceInbox(credential).then(setWorkspaceInbox).catch(() => undefined);
          void listConversationDigests(credential).then(setConversationDigests).catch(() => undefined);
          void listPendingConversationTaskCompletions(credential).then(setPendingTaskCompletions).catch(() => undefined);
        };
        stream.addEventListener("inbox.updated", refreshMessages);
        stream.addEventListener("digest.ready", refreshMessages);
        return stream;
      });
    return () => streams.forEach((stream) => stream.close());
  }, [credential, workspaceStreamKey]);

  /** 提交注册或登录表单，成功后将 JWT 写入 Windows 凭据管理器。 */
  async function submitAuth(event: FormEvent) {
    event.preventDefault();
    if (!/^[A-Za-z0-9_.-]{3,64}$/.test(username)) return setStatus("用户名需为 3-64 位字母、数字、下划线、点或连字符。");
    if (registerMode && password.length < 8) return setStatus("注册密码至少需要 8 位。");
    setBusy(true);
    try {
      await checkHealth(baseUrl);
      const response = registerMode
        ? await register(baseUrl, username, password, displayName || username)
        : await login(baseUrl, username, password);
      const nextCredential = toCredential(baseUrl, response);
      await saveCredential(nextCredential);
      setCredential(nextCredential);
      setStatus("登录成功，正在加载你的工作台…");
    } catch (error) {
      setStatus(error instanceof Error ? error.message : "登录失败");
      setBusy(false);
    }
  }

  /** 创建平台连接并返回连接管理页，后端不会返回凭据明文。 */
  async function submitConnection(event: FormEvent) {
    event.preventDefault();
    if (!credential) return;
    setBusy(true);
    try {
      await createConnection(credential, connectionDraft);
      setConnectionDraft(EMPTY_CONNECTION);
      await loadView("connections", credential);
      setStatus("平台连接已保存，可点击检测连接状态。");
    } catch (error) {
      setStatus(error instanceof Error ? error.message : "保存平台连接失败");
    } finally {
      setBusy(false);
    }
  }

  /** 检查指定平台连接，并刷新连接列表显示新的在线状态。 */
  async function refreshConnection(connectionId: string) {
    if (!credential) return;
    setBusy(true);
    try {
      await checkConnection(credential, connectionId);
      await loadView("connections", credential);
    } catch (error) {
      setStatus(error instanceof Error ? error.message : "检测连接失败");
      setBusy(false);
    }
  }

  /**
   * 打开扫码弹窗并准备 NapCat。
   * 已运行的外部实例会被直接复用；已安装的托管实例会自动启动；首次使用则停在许可确认页。
   */
  async function openNapcatQrLogin() {
    if (!credential) return;
    setQrLoginOpen(true);
    setQrLoginBusy(true);
    setQrLogin(null);
    try {
      const accountId = connections.find((item) => item.platform === "qq" && item.enabled)?.accountId || undefined;
      let runtime: NapcatRuntimeStatus;
      try {
        runtime = await getNapcatRuntimeStatus();
      } catch {
        // 浏览器调试模式没有 Tauri IPC，此时仍允许复用用户已自行启动的 NapCat。
        setQrLogin(await startNapcatQrLogin(credential));
        return;
      }
      setNapcatRuntime(runtime);
      if (!runtime.ready && runtime.installed) {
        runtime = await startNapcatRuntime(accountId);
        setNapcatRuntime(runtime);
      }
      if (runtime.ready) {
        // 先尝试复用上次会话；只有缓存确实失效后才请求二维码。
        setQrLogin({
          state: "RESTORING",
          qrCodeUrl: "",
          message: accountId ? `正在恢复 QQ ${accountId} 的登录状态…` : "正在检查已有 QQ 登录状态…",
          accountId: accountId || "",
          accountName: "",
          onebotConfigured: false,
        });
        const restored = await waitForNapcatSessionRestore(credential, accountId ? 15_000 : 2_000);
        if (restored.state === "CONNECTED") {
          setQrLogin(restored);
          if (restored.accountId) await rememberNapcatAccount(restored.accountId).catch(() => undefined);
        } else {
          setQrLogin(await startNapcatQrLogin(credential));
        }
      } else {
        setQrLogin({
          state: "NAPCAT_OFFLINE",
          qrCodeUrl: "",
          message: "首次连接需要准备 NapCat 官方运行时",
          accountId: "",
          accountName: "",
          onebotConfigured: false,
        });
      }
    } catch (error) {
      setQrLogin({
        state: "ERROR",
        qrCodeUrl: "",
        message: error instanceof Error ? error.message : "无法启动 NapCat 扫码登录",
        accountId: "",
        accountName: "",
        onebotConfigured: false,
      });
    } finally {
      setQrLoginBusy(false);
    }
  }

  /**
   * 用户确认第三方许可后执行完整链路：官方包下载与校验、安装、隐藏启动、获取二维码。
   * 任一步失败都会保留在当前弹窗中，避免用户误以为 QQ 已经接入。
   */
  async function prepareNapcatRuntimeAndLogin() {
    if (!credential || !napcatLicenseAccepted) return;
    setQrLoginBusy(true);
    setQrLogin((current) => ({
      state: "NAPCAT_OFFLINE",
      qrCodeUrl: "",
      message: napcatRuntime?.installed ? "正在启动 NapCat…" : "正在下载并校验 NapCat 官方运行时，首次使用约 110 MB…",
      accountId: current?.accountId || "",
      accountName: current?.accountName || "",
      onebotConfigured: false,
    }));
    try {
      const accountId = connections.find((item) => item.platform === "qq" && item.enabled)?.accountId || undefined;
      let runtime = napcatRuntime;
      if (!runtime?.installed) {
        let progress = await startNapcatRuntimeInstall();
        setNapcatInstallProgress(progress);
        while (progress.state !== "COMPLETED") {
          if (progress.state === "FAILED") {
            throw new Error(progress.error || "NapCat 安装失败");
          }
          setQrLogin((current) => current ? { ...current, message: progress.message } : current);
          await new Promise((resolve) => window.setTimeout(resolve, 400));
          progress = await getNapcatRuntimeInstallProgress();
          setNapcatInstallProgress(progress);
        }
        runtime = await getNapcatRuntimeStatus();
        setNapcatRuntime(runtime);
      }
      if (!runtime.ready) {
        setQrLogin((current) => current ? { ...current, message: "NapCat 已安装，正在启动本地 WebUI…" } : current);
        runtime = await startNapcatRuntime(accountId);
        setNapcatRuntime(runtime);
      }
      const restored = await waitForNapcatSessionRestore(credential, accountId ? 15_000 : 2_000);
      if (restored.state === "CONNECTED") {
        setQrLogin(restored);
        if (restored.accountId) await rememberNapcatAccount(restored.accountId).catch(() => undefined);
      } else {
        setQrLogin(await startNapcatQrLogin(credential));
      }
    } catch (error) {
      const message = error instanceof Error ? error.message : String(error || "NapCat 准备失败");
      setQrLogin({
        state: "ERROR",
        qrCodeUrl: "",
        message,
        accountId: "",
        accountName: "",
        onebotConfigured: false,
      });
    } finally {
      setQrLoginBusy(false);
    }
  }

  /** 二维码过期后主动刷新，并保持弹窗内状态可见。 */
  async function refreshNapcatQrCode() {
    if (!credential) return;
    setQrLoginBusy(true);
    try {
      setQrLogin(await refreshNapcatQrLogin(credential));
    } catch (error) {
      setStatus(error instanceof Error ? error.message : "刷新二维码失败");
    } finally {
      setQrLoginBusy(false);
    }
  }

  /** 弹窗等待快速恢复或扫码时轮询状态；连接成功后记录账号并同步刷新联系人。 */
  useEffect(() => {
    if (!credential || !qrLoginOpen || !["WAITING_SCAN", "RESTORING"].includes(qrLogin?.state || "")) return;
    let stopped = false;
    const timer = window.setInterval(() => {
      void getNapcatQrLoginStatus(credential).then(async (nextState) => {
        if (stopped) return;
        setQrLogin(nextState);
        if (nextState.state === "CONNECTED") {
          if (nextState.accountId) await rememberNapcatAccount(nextState.accountId).catch(() => undefined);
          const nextConnections = await listConnections(credential);
          const contacts = await searchQqContacts(credential, "");
          setConnections(nextConnections);
          setQqContacts(contacts);
          setQqContactsError("");
          setStatus(`QQ ${nextState.accountName || nextState.accountId} 已连接`);
        }
      }).catch((error) => {
        if (!stopped) setStatus(error instanceof Error ? error.message : "读取扫码状态失败");
      });
    }, 1800);
    return () => {
      stopped = true;
      window.clearInterval(timer);
    };
  }, [credential, qrLoginOpen, qrLogin?.state]);

  /**
   * 读取用户主动选择的 QCE 单文件 JSON。文件内容只会在确认预览/导入时发送给本机 Event Center。
   */
  async function selectQceExport(event: ChangeEvent<HTMLInputElement>) {
    const file = event.target.files?.[0];
    event.target.value = "";
    if (!file) return;
    if (!file.name.toLowerCase().endsWith(".json")) {
      setStatus("请选择 QQ Chat Exporter 导出的单文件 JSON。暂不支持 HTML、TXT 和分块 JSONL。");
      return;
    }
    try {
      const content = await file.text();
      const parsed = JSON.parse(content) as unknown;
      if (!parsed || typeof parsed !== "object") throw new Error("JSON 根节点必须是对象");
      setQceExportData(parsed);
      setQceFileName(file.name);
      setQcePreview(null);
      setQceChatIdOverride("");
      setQceChatTypeOverride("");
      setStatus(`已选择 ${file.name}。请先生成导入预览。`);
    } catch (error) {
      setQceExportData(null);
      setQcePreview(null);
      setStatus(error instanceof Error ? `无法读取 QCE JSON：${error.message}` : "无法读取 QCE JSON。");
    }
  }

  /** 预览 QCE 导出文件，群聊缺少群号时可通过覆盖字段映射到现有会话。 */
  async function previewSelectedQceExport() {
    if (!credential || !qceExportData || !qceFileName) return;
    setBusy(true);
    try {
      const preview = await previewQceHistoryImport(credential, {
        exportData: qceExportData,
        sourceName: qceFileName,
        chatIdOverride: qceChatIdOverride.trim() || undefined,
        chatTypeOverride: qceChatTypeOverride || undefined,
      });
      setQcePreview(preview);
      setStatus("QCE 导入预览已生成。确认后仅写入历史上下文，不会触发自动回复。");
    } catch (error) {
      setStatus(error instanceof Error ? error.message : "生成 QCE 导入预览失败。");
    } finally {
      setBusy(false);
    }
  }

  /** 确认写入 QCE 历史事件；写入后刷新消息相关数据，导入记录不会出现在待接管列表。 */
  async function importSelectedQceExport() {
    if (!credential || !qceExportData || !qceFileName || !qcePreview) return;
    setBusy(true);
    try {
      const result = await importQceHistory(credential, {
        exportData: qceExportData,
        sourceName: qceFileName,
        chatIdOverride: qceChatIdOverride.trim() || undefined,
        chatTypeOverride: qceChatTypeOverride || undefined,
      });
      setStatus(`已导入 ${result.importedCount} 条历史消息，跳过 ${result.duplicateCount} 条重复记录。`);
      await loadView("messages", credential);
    } catch (error) {
      setStatus(error instanceof Error ? error.message : "导入 QCE 历史记录失败。");
    } finally {
      setBusy(false);
    }
  }

  /** 自动导入已授权目录中的 QCE JSON；群聊缺少群号时拒绝导入，防止历史写进错误会话。 */
  async function importWatchedQceExport(exportData: unknown, sourceName: string) {
    if (!credential) throw new Error("请先登录本地服务");
    setBusy(true);
    try {
      const preview = await previewQceHistoryImport(credential, { exportData, sourceName });
      if (preview.requiresChatIdMapping) {
        throw new Error(`${sourceName} 缺少群号映射，请使用手动导入完成会话映射`);
      }
      const result = await importQceHistory(credential, { exportData, sourceName });
      await loadView("messages", credential);
      return result;
    } finally {
      setBusy(false);
    }
  }

  /** 保存新建会话设定，并用后端返回的规则刷新当前设定集页面。 */
  async function submitConversationProfile(event: FormEvent) {
    event.preventDefault();
    if (!credential) return;
    const wasEditing = Boolean(editingProfileId);
    setBusy(true);
    try {
      const skillReferences = await validateSkillReferences();
      const skillReference = skillReferences[0] || "";
      const payload = {
        name: profileDraft.name,
        description: profileDraft.systemPrompt,
        enabled: true,
        platform: "qq",
        accountId: "",
        scene: "",
        chatType: profileDraft.chatType,
        chatIds: profileDraft.contactIds,
        targetUserIds: [],
        supportedRoutes: profileDraft.chatType === "group" ? ["social_reply", "group_ops"] : ["social_reply"],
        triggerMode: profileDraft.chatType === "group" ? "AT_SELF_ONLY" : "ALWAYS",
        triggerKeywords: [],
        personaMode: skillReferences.length > 0
          ? "SKILL"
          : (profileDraft.systemPrompt.trim() ? "PROMPT" : "NONE"),
        // Skill 与人格提示可以叠加；提示词作为会话级补充约束，不再因装载 Skill 而丢失。
        systemPrompt: profileDraft.systemPrompt,
        skillReference,
        skillReferences,
        modelProfileId: profileDraft.modelProfileId,
        preferredRoute: "social_reply",
        replyMode: profileDraft.replyMode,
        // 群管理是特权工具，只有用户在当前群聊设定中显式授权后才写入白名单。
        allowedTools: profileDraft.chatType === "group" && profileDraft.groupManagementEnabled
          ? ["manage_qq_group"]
          : [],
        requireHumanConfirmation: profileDraft.replyMode !== "AUTO_REPLY",
        priority: 10,
          notificationMode: profileDraft.notificationMode,
          notificationKeywords: profileDraft.notificationKeywords.split(/[，,\n]/).map((item) => item.trim()).filter(Boolean),
          digestWindowSeconds: profileDraft.digestWindowSeconds,
          digestMaxMessages: profileDraft.digestMaxMessages,
          includeUrgentInDigest: profileDraft.includeUrgentInDigest,
        maxReplyChars: profileDraft.maxReplyChars,
          splitLongReply: profileDraft.splitLongReply,
          splitReplyChancePercent: profileDraft.splitReplyChancePercent,
          privateHistoryEnabled: profileDraft.privateHistoryEnabled,
          historyMaxMessages: profileDraft.historyMaxMessages,
          historyMaxChars: profileDraft.historyMaxChars,
          historyTrainingEnabled: profileDraft.historyTrainingEnabled,
          reviewMode: profileDraft.reviewMode,
          knowledgeBaseSources: profileDraft.knowledgeBaseSources.split(/\r?\n/).map((item) => item.trim()).filter(Boolean),
          profileContext: profileDraft.profileContext,
      };
      let savedProfile: ConversationProfile;
      if (editingProfileId) {
        savedProfile = await updateConversationProfile(credential, editingProfileId, payload);
      } else {
        savedProfile = await createConversationProfile(credential, payload);
      }
      let syncSummary = "";
      if (profileDraft.chatType === "private" && profileDraft.privateHistoryEnabled) {
        // 保存后主动等待一次上下文同步结果，避免仅依赖后端异步任务导致用户不知道历史是否真正入库。
        const contextResult = await syncConversationHistoryContext(credential, savedProfile.id, 100);
        syncSummary = ` 已同步 ${contextResult.importedMessages} 条近期上下文`;
        if (contextResult.duplicateMessages > 0) {
          syncSummary += `，${contextResult.duplicateMessages} 条已存在`;
        }
        if (contextResult.skippedMessages > 0) {
          syncSummary += `，跳过 ${contextResult.skippedMessages} 条无效记录`;
        }
        syncSummary += "。";

        // 上下文同步完成后再生成认知卡；单个会话分析失败不能回滚已经保存的设定。
        const cognitionResults = await Promise.allSettled(profileDraft.contactIds.map((chatId) =>
          refreshConversationCognition(
            credential,
            "qq",
            profileDraft.chatType,
            chatId,
            Math.max(20, Math.min(200, profileDraft.historyMaxMessages)),
          ),
        ));
        const cognitionCount = cognitionResults.filter((result) => result.status === "fulfilled").length;
        if (cognitionCount > 0) syncSummary += ` 已更新 ${cognitionCount} 个会话认知卡。`;
        if (cognitionResults.some((result) => result.status === "rejected")) syncSummary += " 部分认知卡将在收到新消息后重试。";
      }
      if (profileDraft.chatType === "private" && profileDraft.historyTrainingEnabled) {
        const syncResult = await syncConversationHistoryTraining(credential, savedProfile.id, 100);
        syncSummary += ` 已同步 ${syncResult.importedMessages} 条历史训练样本。`;
        if (syncResult.personalSkillAvailable) {
          syncSummary += ` “我的表达风格”Skill 已可选择（置信度 ${Math.round(syncResult.confidence * 100)}%）。`;
        } else {
          syncSummary += ` 当前有效样本 ${syncResult.eligibleSampleCount} 条，尚未达到稳定发布条件。`;
        }
      }
      setProfileDraft(EMPTY_PROFILE);
      setProfileEditorOpen(false);
      setEditingProfileId("");
      await loadView("profiles", credential);
      setStatus((wasEditing ? "会话设定已更新。" : "会话设定已保存。") + syncSummary);
    } catch (error) {
      setStatus(error instanceof Error ? error.message : "保存会话设定失败");
    } finally {
      setBusy(false);
    }
  }

  /** 打开模型配置编辑器；传入现有配置时不会把脱敏密钥回填到输入框。 */
  function openModelEditor(profile?: ModelProfile) {
    setEditingModelId(profile?.id || "");
    setModelDraft(profile ? {
      name: profile.name,
      description: profile.description,
      enabled: profile.enabled,
      provider: profile.provider,
      baseUrl: profile.baseUrl,
      apiKey: "",
      model: profile.model,
      temperature: profile.temperature ?? 0.4,
      maxTokens: profile.maxTokens ?? 2048,
      supportedRoutes: profile.supportedRoutes,
      isDefault: profile.isDefault,
      priority: profile.priority,
    } : EMPTY_MODEL);
    setModelEditorOpen(true);
  }

  /** 使用内置模型预设打开编辑器，用户只需补充 API Key 和个性化参数。 */
  function openModelPreset(preset: ModelPreset) {
    setEditingModelId("");
    setModelDraft({
      name: preset.name, description: preset.note, enabled: true, provider: preset.provider,
      baseUrl: preset.baseUrl, apiKey: "", model: preset.model, temperature: preset.temperature,
      maxTokens: preset.maxTokens, supportedRoutes: preset.recommendedRoutes || [], isDefault: modelProfiles.length === 0, priority: 0,
    });
    setModelEditorOpen(true);
  }

  /** 关闭模型编辑器并清空敏感输入，避免 API Key 留在 React 状态中。 */
  function closeModelEditor() {
    setModelEditorOpen(false);
    setEditingModelId("");
    setModelDraft(EMPTY_MODEL);
  }

  /** 保存新增或编辑后的模型配置，并立即刷新默认模型和 route 绑定状态。 */
  async function submitModelProfile(event: FormEvent) {
    event.preventDefault();
    if (!credential) return;
    const wasEditing = Boolean(editingModelId);
    setBusy(true);
    try {
      if (editingModelId) {
        await updateModelProfile(credential, editingModelId, modelDraft);
      } else {
        await createModelProfile(credential, modelDraft);
      }
      closeModelEditor();
      await loadView("models", credential);
      setStatus(wasEditing ? "模型配置已更新。" : "模型配置已创建。");
    } catch (error) {
      setStatus(error instanceof Error ? error.message : "保存模型配置失败");
    } finally {
      setBusy(false);
    }
  }

  /** 删除当前用户拥有的模型配置，删除前使用原生确认框防止误操作。 */
  async function removeModelProfile(profile: ModelProfile) {
    if (!credential || !window.confirm(`确认删除模型配置“${profile.name}”吗？`)) return;
    setBusy(true);
    try {
      await deleteModelProfile(credential, profile.id);
      await loadView("models", credential);
      setStatus("模型配置已删除。");
    } catch (error) {
      setStatus(error instanceof Error ? error.message : "删除模型配置失败");
    } finally {
      setBusy(false);
    }
  }

  /** 打开候选记忆编辑器；编辑时只复制允许用户修改的结构化字段。 */
  function openMemoryEditor(item?: MemoryCandidate) {
    setEditingMemoryId(item?.id || "");
    setMemoryDraft(item ? {
      subject: item.subject,
      predicate: item.predicate,
      value: item.value,
      scopeType: item.scopeType,
      platform: item.platform || "",
      scene: item.scene || "",
      chatType: item.chatType || "",
      chatId: item.chatId || "",
      expiresAt: toDatetimeLocal(item.expiresAt),
    } : EMPTY_MEMORY);
    setMemoryEditorOpen(true);
  }

  /** 手工建立或修改候选记忆；候选仍需再次确认，不会立即注入 Agent。 */
  async function submitMemoryCandidate(event: FormEvent) {
    event.preventDefault();
    if (!credential) return;
    setBusy(true);
    try {
      if (editingMemoryId) {
        await updateMemoryCandidate(credential, editingMemoryId, memoryDraft);
      } else {
        await createMemoryCandidate(credential, memoryDraft);
      }
      setMemoryDraft(EMPTY_MEMORY);
      setEditingMemoryId("");
      setMemoryEditorOpen(false);
      await loadView("memories", credential);
      setStatus(editingMemoryId ? "候选记忆已修改，请重新核对。" : "候选记忆已创建，请确认后再提供给 Agent。");
    } catch (error) {
      setStatus(error instanceof Error ? error.message : "创建候选记忆失败");
    } finally {
      setBusy(false);
    }
  }

  /** 确认一条候选事实，使其从下一轮开始可以进入匹配作用域的 Agent 上下文。 */
  async function confirmMemoryCandidate(item: MemoryCandidate) {
    if (!credential || !window.confirm(`确认将“${item.subject} / ${item.predicate}”作为长期事实吗？`)) return;
    setBusy(true);
    try {
      await verifyMemoryCandidate(credential, item.id);
      await loadView("memories", credential);
      setStatus("长期记忆已确认。");
    } catch (error) {
      setStatus(error instanceof Error ? error.message : "确认长期记忆失败");
    } finally {
      setBusy(false);
    }
  }

  /** 按需读取候选记忆的来源消息窗口，并交给统一聊天上下文弹窗展示。 */
  async function openMemoryEvidence(item: MemoryCandidate) {
    if (!credential) return;
    setMemoryEvidenceItem(item);
    setMemoryEvidenceOpen(true);
    setMemoryEvidenceLoading(true);
    setMemoryEvidenceError("");
    setMemoryEvidence(null);
    try {
      setMemoryEvidence(await getMemoryCandidateEvidence(credential, item.id));
    } catch (error) {
      setMemoryEvidenceError(error instanceof Error ? error.message : "读取记忆来源失败");
    } finally {
      setMemoryEvidenceLoading(false);
    }
  }

  /** 按事件读取 Runtime 执行轨迹；调用方只能据此展示真实使用过的长期记忆，不能自行推断。 */
  async function loadStoredEventDetail(eventId: string): Promise<StoredEventDetail> {
    if (!credential) throw new Error("登录状态已失效，请重新登录");
    return getStoredEventDetail(credential, eventId);
  }

  /** 提交明确冲突决策；服务端会在一个事务中拒绝候选或替代旧值。 */
  async function resolveCandidateConflict(
    item: MemoryCandidate,
    decision: "KEEP_VERIFIED" | "USE_CANDIDATE",
  ) {
    if (!credential) return;
    setBusy(true);
    try {
      await resolveMemoryConflict(credential, item.id, decision);
      await loadView("memories", credential);
      setStatus(decision === "USE_CANDIDATE" ? "已采用候选值，旧值已归档。" : "已保留原确认值，候选已拒绝。");
    } catch (error) {
      setStatus(error instanceof Error ? error.message : "处理记忆冲突失败");
      throw error;
    } finally {
      setBusy(false);
    }
  }

  /** 拒绝错误候选；拒绝记录保留在列表中用于解释为什么没有进入 Agent。 */
  async function declineMemoryCandidate(item: MemoryCandidate) {
    if (!credential) return;
    const reason = window.prompt("拒绝原因（可选）", "信息不准确或不应长期保存");
    if (reason === null) return;
    setBusy(true);
    try {
      await rejectMemoryCandidate(credential, item.id, reason);
      await loadView("memories", credential);
      setStatus("候选记忆已拒绝。");
    } catch (error) {
      setStatus(error instanceof Error ? error.message : "拒绝长期记忆失败");
    } finally {
      setBusy(false);
    }
  }

  /** 永久删除记忆记录；该操作不会删除作为证据的原始聊天事件。 */
  async function removeMemoryCandidate(item: MemoryCandidate) {
    if (!credential || !window.confirm(`确认删除“${item.subject} / ${item.predicate}”吗？`)) return;
    setBusy(true);
    try {
      await deleteMemoryCandidate(credential, item.id);
      await loadView("memories", credential);
      setStatus("长期记忆已删除。");
    } catch (error) {
      setStatus(error instanceof Error ? error.message : "删除长期记忆失败");
    } finally {
      setBusy(false);
    }
  }

  /** 保存设定前让后端按真实 Runtime 规则解析 Skill，防止无效引用静默写入。 */
  async function validateSkillReferences() {
    if (!credential) return [];
    const references = [...new Set(profileDraft.skillReferences.map((item) => item.trim()).filter(Boolean))];
    if (references.length === 0) return [];

    const routeIncompatible = skills.filter((skill) =>
      references.includes(skill.reference)
      && skill.applicableRoutes.length > 0
      && !skill.applicableRoutes.includes("social_reply"),
    );
    if (routeIncompatible.length > 0) {
      throw new Error(`以下 Skill 不适用于社交回复：${routeIncompatible.map((item) => item.name).join("、")}`);
    }

    const preview = await previewSkillResolution(credential, references, "social_reply");
    if (preview.unresolvedSkillReferences.length > 0) {
      throw new Error(`以下 Skill 尚未安装或无法解析：${preview.unresolvedSkillReferences.join("、")}`);
    }
    if (preview.resolvedSkills.length !== references.length) {
      throw new Error("部分 Skill 未通过 social_reply 路由校验，请检查其适用场景。");
    }
    return references;
  }

  /** 用户明确点击后安装 GitHub Skill，并立即把校验通过的描述符加入当前设定。 */
  async function installProfileGithubSkill(): Promise<string> {
    if (!credential) throw new Error("登录状态已失效，请重新登录后再安装 Skill。");
    const reference = profileDraft.githubReference.trim();
    if (!reference) {
      throw new Error("请先填写 GitHub 仓库 URL 或 github:// 引用。");
    }
    setBusy(true);
    try {
      const result = await installGithubSkill(credential, reference);
      const preview = await previewSkillResolution(credential, [result.descriptor.reference], "social_reply");
      if (preview.resolvedSkills.length !== 1) {
        throw new Error("Skill 已下载，但不适用于 social_reply，未加入当前设定。");
      }
      setSkills((current) => [result.descriptor, ...current.filter((item) => item.reference !== result.descriptor.reference)]);
      setProfileDraft((current) => ({
        ...current,
        githubReference: "",
        skillReferences: [...new Set([...current.skillReferences, result.descriptor.reference])],
        skillReference: current.skillReference || result.descriptor.reference,
      }));
      const message = `Skill“${result.descriptor.name}”已安装并加入当前设定。`;
      setStatus(message);
      return message;
    } catch (error) {
      const message = error instanceof Error ? error.message : "安装 GitHub Skill 失败";
      setStatus(message);
      throw error instanceof Error ? error : new Error(message);
    } finally {
      setBusy(false);
    }
  }

  /**
   * 打开新建设定编辑器，同时拉取可搜索的 QQ 联系人与本地 Skill 清单。
   */
  async function openProfileEditor(profile?: ConversationProfile) {
    if (!credential) return;
    setEditingProfileId(profile?.id || "");
    setProfileEditorOpen(true);
    setBusy(true);
    try {
      const [contacts, nextSkills, nextAssets] = await Promise.all([
        searchQqContacts(credential, ""),
        listSkills(credential),
        listSecureAssets(credential),
      ]);
      const existingSkillReferences = profile
        ? (profile.skillReferences?.length ? profile.skillReferences : [profile.skillReference].filter(Boolean))
        : [];
      const selectedSkill = nextSkills.find((item) => existingSkillReferences.includes(item.reference));
      setProfileDraft(profile ? {
      name: profile.name,
      chatType: profile.chatType || "private",
      contactIds: profile.chatIds || [],
      systemPrompt: profile.systemPrompt || "",
      replyMode: profile.replyMode || "DRAFT_ONLY",
        skillMode: profile.skillReference
          ? (selectedSkill?.sourceType === "personal" ? "personal" : "local")
          : "prompt",
      skillReference: profile.skillReference || "",
      skillReferences: existingSkillReferences,
      githubReference: "",
      modelProfileId: profile.modelProfileId || "",
      maxReplyChars: profile.maxReplyChars || 24,
       splitLongReply: profile.splitLongReply !== false,
       splitReplyChancePercent: profile.splitReplyChancePercent ?? 33,
       privateHistoryEnabled: profile.privateHistoryEnabled === true,
       historyMaxMessages: profile.historyMaxMessages ?? 12,
       historyMaxChars: profile.historyMaxChars ?? 2000,
         historyTrainingEnabled: profile.historyTrainingEnabled === true,
         notificationMode: profile.notificationMode || "AUTO",
         notificationKeywords: (profile.notificationKeywords || []).join("，"),
         digestWindowSeconds: profile.digestWindowSeconds || 1800,
         digestMaxMessages: profile.digestMaxMessages || 20,
         includeUrgentInDigest: profile.includeUrgentInDigest === true,
         reviewMode: profile.reviewMode || "STRICT_HANDOFF",
         knowledgeBaseSources: (profile.knowledgeBaseSources || []).join("\n"),
         groupManagementEnabled: (profile.allowedTools || []).includes("manage_qq_group"),
         profileContext: profile.profileContext || EMPTY_PROFILE_CONTEXT,
      } : EMPTY_PROFILE);
      setQqContacts(contacts);
      setSkills(nextSkills);
      setSecureAssets(nextAssets);
    } catch (error) {
      setStatus(error instanceof Error ? error.message : "读取联系人或 Skill 失败");
    } finally {
      setBusy(false);
    }
  }

  /** 删除当前用户拥有的设定，并刷新列表；确认框用于避免误操作。 */
  async function removeConversationProfile(profile: ConversationProfile) {
    if (!credential || !window.confirm(`确认删除设定“${profile.name}”吗？`)) return;
    setBusy(true);
    try {
      await deleteConversationProfile(credential, profile.id);
      await loadView("profiles", credential);
      setStatus("会话设定已删除。");
    } catch (error) {
      setStatus(error instanceof Error ? error.message : "删除会话设定失败");
    } finally {
      setBusy(false);
    }
  }

  /** 创建或更新安全资产，并刷新元数据列表；敏感正文不会从服务端响应中返回。 */
  async function saveSecureAsset(assetId: string, draft: SecureAssetDraft) {
    if (!credential) return;
    setBusy(true);
    try {
      if (assetId) await updateSecureAsset(credential, assetId, draft);
      else await createSecureAsset(credential, draft);
      setSecureAssets(await listSecureAssets(credential));
      setStatus(assetId ? "安全资产已更新。" : "安全资产已加密保存。");
    } finally {
      setBusy(false);
    }
  }

  /** 删除资产前进行二次确认，并移除当前尚未保存表单中的失效引用。 */
  async function removeSecureAsset(asset: SecureAsset) {
    if (!credential) return;
    const usedByDraft = profileDraft.profileContext.assets.some((reference) => reference.assetId === asset.id);
    const warning = usedByDraft
      ? `资产“${asset.name}”正被当前设定引用。删除后该引用会失效，仍要继续吗？`
      : `确认删除安全资产“${asset.name}”吗？此操作不能恢复。`;
    if (!window.confirm(warning)) return;
    setBusy(true);
    try {
      await deleteSecureAsset(credential, asset.id);
      setSecureAssets(await listSecureAssets(credential));
      setProfileDraft((current) => ({
        ...current,
        profileContext: {
          ...current.profileContext,
          assets: current.profileContext.assets.filter((reference) => reference.assetId !== asset.id),
        },
      }));
      setStatus("安全资产已删除。");
    } finally {
      setBusy(false);
    }
  }

  /**
   * 保存独立的消息监控范围。私聊和群聊分别使用系统规则，避免与人格设定、自动回复规则混在一起。
   */
  async function saveMonitoringSelection(privateIds: string[], groupIds: string[]) {
    if (!credential) return;
    setBusy(true);
    try {
      const existing = conversationProfiles.filter((item) => item.description === MONITOR_PROFILE_MARKER);
      for (const [chatType, chatIds] of [["private", privateIds], ["group", groupIds]] as const) {
        const payload = {
          name: chatType === "private" ? "私聊消息监控" : "群聊消息监控",
          description: MONITOR_PROFILE_MARKER,
          enabled: chatIds.length > 0,
          platform: "qq",
          scene: chatType === "private" ? "social" : "life",
          chatType,
          chatIds,
          targetUserIds: [], supportedRoutes: ["chat_summary"], triggerMode: "ALWAYS", triggerKeywords: [],
          personaMode: "NONE", systemPrompt: "", skillReference: "", skillReferences: [], modelProfileId: "",
          preferredRoute: "chat_summary", replyMode: "DRAFT_ONLY", allowedTools: [], requireHumanConfirmation: true,
          priority: 5, notificationMode: "AUTO", notificationKeywords: [], digestWindowSeconds: 1800,
          digestMaxMessages: 20, includeUrgentInDigest: false, maxReplyChars: 24, splitLongReply: false,
          splitReplyChancePercent: 0, privateHistoryEnabled: false, historyMaxMessages: 12,
          historyMaxChars: 2000, historyTrainingEnabled: false,
        };
        const current = existing.find((item) => item.chatType === chatType);
        if (current) await updateConversationProfile(credential, current.id, payload);
        else await createConversationProfile(credential, payload);
      }
      await loadView("monitoring", credential);
      setStatus("消息监控范围已保存。");
    } catch (error) {
      setStatus(error instanceof Error ? error.message : "保存消息监控范围失败");
    } finally {
      setBusy(false);
    }
  }

  /** 发送用户在接管卡片中亲自编辑的回复，发送后暂不刷新，以便继续选择代理状态。 */
  async function sendHumanHandoffReply(eventId: string, message: string) {
    if (!credential || !message.trim()) return;
    await confirmInboxDraft(credential, eventId, message.trim());
  }

  /** 保存人工发送后的代理选择，并刷新消息空间移除已完成接管事项。 */
  async function finishHumanHandoff(item: WorkspaceInboxItem, continueAgent: boolean) {
    if (!credential) return;
    await updateConversationAgentState(credential, item.platform, item.chatType, item.chatId, continueAgent);
    await loadView("messages", credential);
    setStatus(continueAgent ? "人工回复已发送，Agent 将继续代理该会话。" : "人工回复已发送，该会话 Agent 代理已暂停。");
  }

  /** 审批 Agent 提交的任务完成申请；通过后停止该会话代理，拒绝后恢复任务推进。 */
  async function decideProxyTaskCompletion(item: ConversationProxyTaskState, approved: boolean) {
    if (!credential) throw new Error("登录状态已失效，请重新登录。");
    await decideConversationTaskCompletion(credential, item.profileId, item.chatId, approved);
    setPendingTaskCompletions((current) => current.filter(
      (candidate) => candidate.profileId !== item.profileId || candidate.chatId !== item.chatId,
    ));
    await loadView("messages", credential);
    setStatus(approved
      ? "任务完成申请已通过，该会话 Agent 代理已结束。"
      : "任务仍需继续，Agent 将保留已有进度并继续代理。");
  }

  /** 读取当前用户拥有的事件所对应的群管理审批摘要。 */
  async function loadPendingGroupOperation(eventId: string) {
    if (!credential) throw new Error("登录状态已失效，请重新登录。");
    return getPendingGroupOperation(credential, eventId);
  }

  /** 提交群管理确认短语；成功后刷新消息空间移除已完成审批。 */
  async function approveGroupOperation(eventId: string, confirmationText: string) {
    if (!credential) throw new Error("登录状态已失效，请重新登录。");
    await approvePendingGroupOperation(credential, eventId, confirmationText);
    await loadView("messages", credential);
    setStatus("群管理操作已审批并执行。");
  }

  /**
   * 仅在用户主动查看某个会话时读取最新上下文并生成进度概括。
   * 该请求不会被页面刷新、SSE 或后台定时器调用，避免频繁消耗模型额度。
   */
  async function loadConversationProgress(platform: string, chatType: string, chatId: string, lastSeenAgentEventId = "") {
    if (!credential) throw new Error("登录状态已失效，请重新登录。");
    return getConversationProgress(credential, platform, chatType, chatId, 60, lastSeenAgentEventId);
  }

  /** 手动创建日程并重新读取简报，使新增项立即出现在消息空间。 */
  async function addWorkspaceSchedule(draft: WorkspaceScheduleDraft) {
    if (!credential) throw new Error("登录状态已失效，请重新登录。");
    await createWorkspaceSchedule(credential, draft);
    await loadView("messages", credential);
    setStatus("日程已创建，并同步到近期日程。");
  }

  /** 删除当前用户拥有的日程并刷新消息空间；服务端会再次校验来源所有权。 */
  async function removeWorkspaceSchedule(scheduleId: string) {
    if (!credential) throw new Error("登录状态已失效，请重新登录。");
    await deleteWorkspaceSchedule(credential, scheduleId);
    await loadView("messages", credential);
    setStatus("日程已删除。");
  }

  /** 用户点击来源时才加载原消息附近的真实会话上下文。 */
  async function loadWorkspaceScheduleSource(scheduleId: string) {
    if (!credential) throw new Error("登录状态已失效，请重新登录。");
    return getWorkspaceScheduleSourceContext(credential, scheduleId, 3);
  }

  /** 清除安全存储的 JWT，并回到登录页面。 */
  async function logout() {
    await removeCredential();
    setCredential(null);
    setConnections([]);
    setModelProfiles([]);
    setStatus("本地登录状态已清除");
  }

  /** 将首页输入交给安全的工作台命令接口，客户端不会绕过 Java 服务直接访问 Python Runtime。 */
  async function runWorkspaceCommand(prompt: string, requestedRoute: string) {
    if (!credential) throw new Error("登录状态已失效，请重新登录。");
    return executeWorkspaceCommand(credential, prompt, requestedRoute);
  }

  /** 查询当前账户的最近委托任务，确保客户端刷新后仍能恢复待处理状态。 */
  async function loadWorkspaceDelegatedTasks(limit = 12) {
    if (!credential) throw new Error("登录状态已失效，请重新登录。");
    return listDelegatedTasks(credential, limit);
  }

  /** 查询单个委托任务的完整运行状态，详情弹窗会用它刷新时间线和进度。 */
  async function loadWorkspaceDelegatedTask(taskId: string) {
    if (!credential) throw new Error("登录状态已失效，请重新登录。");
    return getDelegatedTask(credential, taskId);
  }

  /** 确认委托任务只会进入 READY 队列，真正发送动作由后续受控执行器完成。 */
  async function confirmWorkspaceDelegatedTask(taskId: string) {
    if (!credential) throw new Error("登录状态已失效，请重新登录。");
    return confirmDelegatedTask(credential, taskId);
  }

  /** 取消当前委托任务并返回最新状态。 */
  async function cancelWorkspaceDelegatedTask(taskId: string) {
    if (!credential) throw new Error("登录状态已失效，请重新登录。");
    return cancelDelegatedTask(credential, taskId);
  }

  /** 根据用户操作切换委托任务状态，所有控制动作都经过 Event Center 鉴权。 */
  async function controlWorkspaceDelegatedTask(taskId: string, action: DelegatedTaskControlAction) {
    if (!credential) throw new Error("登录状态已失效，请重新登录。");
    if (action === "pause") return pauseDelegatedTask(credential, taskId);
    if (action === "resume") return resumeDelegatedTask(credential, taskId);
    if (action === "complete") return completeDelegatedTask(credential, taskId);
    return cancelDelegatedTask(credential, taskId);
  }

  if (!credential) {
    return (
      <main className="auth-shell">
        <section className="brand-panel"><p className="eyebrow">PERSONAL AGENT DESKTOP</p><h1>Memo<br />Echo</h1><p>把散落在聊天、任务和日程里的信息，整理成你真正能执行的一天。</p><span className="signal">LOCAL FIRST / USER CONTROLLED</span></section>
        <section className="auth-card"><div className="auth-heading"><p>连接你的本地服务</p><h2>{registerMode ? "创建本地账户" : "欢迎回来"}</h2></div>
          <form onSubmit={submitAuth}>
            <label>Event Center 地址<input value={baseUrl} onChange={(event) => setBaseUrl(event.target.value)} required /></label>
            <label>用户名<input value={username} onChange={(event) => setUsername(event.target.value)} pattern="[A-Za-z0-9_.-]{3,64}" required /></label>
            {registerMode && <label>显示名称<input value={displayName} onChange={(event) => setDisplayName(event.target.value)} /></label>}
            <label>密码<input type="password" value={password} onChange={(event) => setPassword(event.target.value)} minLength={registerMode ? 8 : undefined} required /></label>
            <button disabled={busy} type="submit">{busy ? "正在连接…" : registerMode ? "注册并进入" : "登录客户端"}</button>
          </form>
          <button className="text-button" onClick={() => setRegisterMode(!registerMode)} type="button">{registerMode ? "已有账户，直接登录" : "首次使用，创建账户"}</button><p className="status-line">{status}</p>
        </section>
      </main>
    );
  }

  const unseenInboxHandoffCount = (workspaceInbox?.items || []).filter(
    (item) => item.needHumanConfirmation && !seenHandoffIds.includes(item.eventId),
  ).length;
  const unseenTaskCompletionCount = pendingTaskCompletions.filter(
    (item) => !seenHandoffIds.includes(buildTaskCompletionSeenId(item)),
  ).length;
  const unseenHandoffCount = unseenInboxHandoffCount + unseenTaskCompletionCount;
  const pendingMemoryCount = memoryCandidates.filter((item) => item.status === "CANDIDATE").length;

  return (
    <main className="desktop-shell">
      <aside className="app-sidebar">
        <div className="sidebar-brand">
          <span className="brand-symbol">M</span>
          <div><strong>Memo Echo</strong><small>Personal agent</small></div>
        </div>
        <button className="new-task-button" onClick={() => setActiveView("console")} type="button">
          <Plus size={18} weight="bold" />新建任务
        </button>
        <nav className="primary-nav" aria-label="主导航">
          <p>工作台</p>
          <button className={activeView === "console" ? "active" : ""} onClick={() => setActiveView("console")}><ChatText size={19} /><span>主控台</span></button>
          <button className={activeView === "dashboard" ? "active" : ""} onClick={() => setActiveView("dashboard")}><House size={19} /><span>今日脉搏</span></button>
          <button className={activeView === "messages" ? "active" : ""} onClick={() => setActiveView("messages")}><ChatCircleDots size={19} /><span>消息空间</span>{unseenHandoffCount > 0 && <i className="nav-alert-dot" aria-label={`${unseenHandoffCount} 条未查看接管事项`} />}</button>
            <button className={activeView === "monitoring" ? "active" : ""} onClick={() => setActiveView("monitoring")}><Bell size={19} /><span>消息监控</span></button>
          <button className={activeView === "profiles" ? "active" : ""} onClick={() => setActiveView("profiles")}><SlidersHorizontal size={19} /><span>设定集</span></button>
          <button className={activeView === "memories" ? "active" : ""} onClick={() => setActiveView("memories")}><Brain size={19} /><span>长期记忆</span>{pendingMemoryCount > 0 && <i className="nav-count-badge" aria-label={`${pendingMemoryCount} 条待审核长期记忆`}>{pendingMemoryCount > 99 ? "99+" : pendingMemoryCount}</i>}</button>
          <button className={activeView === "models" ? "active" : ""} onClick={() => setActiveView("models")}><Cpu size={19} /><span>模型配置</span></button>
          <button className={activeView === "connections" ? "active" : ""} onClick={() => setActiveView("connections")}><PlugsConnected size={19} /><span>连接管理</span></button>
        </nav>
        <div className="sidebar-spacer" />
        <div className="sidebar-user">
          <span className="user-avatar">{(credential.displayName || credential.username).slice(0, 1).toUpperCase()}</span>
          <div><strong>{credential.displayName}</strong><small>本地账户</small></div>
          <button title="退出登录" aria-label="退出登录" onClick={logout}><SignOut size={18} /></button>
        </div>
      </aside>
      <section className="workspace">
        <header className="workspace-topbar">
          <div className="workspace-location"><span>Memo Echo</span><i>/</i><strong>{VIEW_LABELS[activeView]}</strong></div>
          <button className="refresh-button" onClick={() => void loadView(activeView, credential)} disabled={busy}>
            <ArrowClockwise className={busy ? "spinning" : ""} size={17} />{busy ? "刷新中" : "刷新"}
          </button>
        </header>
        <div className="workspace-content">
          {activeView === "console" && <WorkspaceConsole credential={credential} />}
          {activeView === "dashboard" && <Dashboard connections={connections} models={modelProfiles} status={status} onExecute={runWorkspaceCommand} onListTasks={loadWorkspaceDelegatedTasks} onGetTask={loadWorkspaceDelegatedTask} onConfirmTask={confirmWorkspaceDelegatedTask} onControlTask={controlWorkspaceDelegatedTask} onOpenModels={() => setActiveView("models")} onOpenProfiles={() => setActiveView("profiles")} onOpenConnections={() => setActiveView("connections")} />}
            {activeView === "messages" && <Messages cacheScope={credential.userId} conversations={conversations} inbox={workspaceInbox} taskCompletions={pendingTaskCompletions} digests={conversationDigests} briefing={workspaceBriefing} status={status} memories={memoryCandidates} onRefresh={() => loadView("messages", credential)} onHandoffsViewed={markHandoffsViewed} onSendHandoff={sendHumanHandoffReply} onFinishHandoff={finishHumanHandoff} onDecideTaskCompletion={decideProxyTaskCompletion} onLoadGroupOperation={loadPendingGroupOperation} onApproveGroupOperation={approveGroupOperation} onLoadConversationProgress={loadConversationProgress} onLoadEventDetail={loadStoredEventDetail} onViewMemoryEvidence={(item) => void openMemoryEvidence(item)} onCreateSchedule={addWorkspaceSchedule} onDeleteSchedule={removeWorkspaceSchedule} onLoadScheduleSource={loadWorkspaceScheduleSource} busy={busy} />}
            {activeView === "monitoring" && <Monitoring contacts={qqContacts} profiles={conversationProfiles.filter((item) => item.description === MONITOR_PROFILE_MARKER)} error={qqContactsError} busy={busy} onRetry={() => credential ? loadView("monitoring", credential) : Promise.resolve()} onSave={saveMonitoringSelection} />}
            {activeView === "profiles" && <><ProfileComposer open={profileEditorOpen} editing={Boolean(editingProfileId)} draft={profileDraft} onDraftChange={setProfileDraft} onSubmit={submitConversationProfile} onInstallGithub={installProfileGithubSkill} onClose={() => { setProfileEditorOpen(false); setEditingProfileId(""); setProfileDraft(EMPTY_PROFILE); }} busy={busy} contacts={qqContacts} contactKeyword={contactKeyword} onContactKeywordChange={setContactKeyword} skills={skills} models={modelProfiles} secureAssets={secureAssets} onSaveSecureAsset={saveSecureAsset} onDeleteSecureAsset={removeSecureAsset} /><Profiles profiles={conversationProfiles.filter((item) => item.description !== MONITOR_PROFILE_MARKER)} models={modelProfiles} onCreate={() => void openProfileEditor()} onEdit={(profile) => void openProfileEditor(profile)} onDelete={(profile) => void removeConversationProfile(profile)} /></>}
          {activeView === "memories" && <Memories items={memoryCandidates} draft={memoryDraft} editorOpen={memoryEditorOpen} editing={Boolean(editingMemoryId)} busy={busy} onCreate={() => openMemoryEditor()} onEdit={openMemoryEditor} onClose={() => { setMemoryEditorOpen(false); setEditingMemoryId(""); setMemoryDraft(EMPTY_MEMORY); }} onDraftChange={setMemoryDraft} onSubmit={submitMemoryCandidate} onVerify={(item) => void confirmMemoryCandidate(item)} onReject={(item) => void declineMemoryCandidate(item)} onDelete={(item) => void removeMemoryCandidate(item)} onViewEvidence={(item) => void openMemoryEvidence(item)} onResolveConflict={resolveCandidateConflict} />}
          {activeView === "models" && <Models profiles={modelProfiles} draft={modelDraft} editorOpen={modelEditorOpen} editingId={editingModelId} busy={busy} onCreate={() => openModelEditor()} onPreset={openModelPreset} onEdit={openModelEditor} onDelete={(profile) => void removeModelProfile(profile)} onDraftChange={setModelDraft} onSubmit={submitModelProfile} onClose={closeModelEditor} />}
          {activeView === "connections" && <><Connections connections={connections} draft={connectionDraft} qrLogin={qrLogin} qrLoginOpen={qrLoginOpen} qrLoginBusy={qrLoginBusy} napcatRuntime={napcatRuntime} napcatInstallProgress={napcatInstallProgress} napcatLicenseAccepted={napcatLicenseAccepted} onNapcatLicenseAccepted={setNapcatLicenseAccepted} onPrepareNapcatRuntime={() => void prepareNapcatRuntimeAndLogin()} onOpenQrLogin={() => void openNapcatQrLogin()} onCloseQrLogin={() => setQrLoginOpen(false)} onRefreshQrLogin={() => void refreshNapcatQrCode()} onDraftChange={setConnectionDraft} onSubmit={submitConnection} onCheck={refreshConnection} busy={busy} /><QceHistoryImporter fileName={qceFileName} preview={qcePreview} chatIdOverride={qceChatIdOverride} chatTypeOverride={qceChatTypeOverride} onFileSelected={selectQceExport} onChatIdOverrideChange={setQceChatIdOverride} onChatTypeOverrideChange={setQceChatTypeOverride} onPreview={previewSelectedQceExport} onImport={importSelectedQceExport} onAutoImport={importWatchedQceExport} onStatus={setStatus} busy={busy} /></>}
          <ConversationContextDialog open={memoryEvidenceOpen} contactName={memoryEvidenceItem ? `${memoryEvidenceItem.subject} · ${memoryEvidenceItem.predicate}` : "记忆来源"} platform={memoryEvidenceItem?.platform || "memory"} snapshot={memoryEvidence ? { summary: memoryEvidenceItem ? `候选事实：${memoryEvidenceItem.value}${memoryEvidence.missingEventIds.length > 0 ? `；${memoryEvidence.missingEventIds.length} 条来源已无法回查` : ""}` : "", generatedByModel: false, generatedAt: new Date().toISOString(), summaryUpdated: false, latestAgentEventId: null, messages: memoryEvidence.messages } : null} loading={memoryEvidenceLoading} error={memoryEvidenceError} onClose={() => setMemoryEvidenceOpen(false)} onRetry={() => { if (memoryEvidenceItem) void openMemoryEvidence(memoryEvidenceItem); }} headerMeta="记忆来源证据" summaryTitle="候选事实" summaryBadge="原始证据" loadingTitle="正在读取来源消息" loadingDescription="正在校验来源事件归属并截取相邻聊天上下文" emptyText="这条候选没有可展示的聊天证据" highlightEventIds={memoryEvidence?.sourceEventIds || []} />
        </div>
      </section>
    </main>
  );
}

/** 渲染更接近桌面 Agent 的首页：能力入口、指令输入和环境状态分层展示。 */
function Dashboard({ connections, models, status, onExecute, onListTasks, onGetTask, onConfirmTask, onControlTask, onOpenModels, onOpenProfiles, onOpenConnections }: {
  connections: PlatformConnection[];
  models: ModelProfile[];
  status: string;
  onExecute: (prompt: string, requestedRoute: string) => Promise<WorkspaceCommandResponse>;
  onListTasks: (limit?: number) => Promise<DelegatedTask[]>;
  onGetTask: (taskId: string) => Promise<DelegatedTask>;
  onConfirmTask: (taskId: string) => Promise<DelegatedTask>;
  onControlTask: (taskId: string, action: DelegatedTaskControlAction) => Promise<DelegatedTask>;
  onOpenModels: () => void;
  onOpenProfiles: () => void;
  onOpenConnections: () => void;
}) {
  const [mode, setMode] = useState("assistant");
  const [prompt, setPrompt] = useState("");
  const [composerNotice, setComposerNotice] = useState("");
  const [requestedRoute, setRequestedRoute] = useState("");
  const [executing, setExecuting] = useState(false);
  const [commandResult, setCommandResult] = useState<WorkspaceCommandResponse | null>(null);
  const [delegatedTasks, setDelegatedTasks] = useState<DelegatedTask[]>([]);
  const [tasksLoading, setTasksLoading] = useState(true);
  const [tasksError, setTasksError] = useState("");
  const [taskActionId, setTaskActionId] = useState("");
  const [taskListMode, setTaskListMode] = useState<"active" | "history">("active");
  const [selectedTask, setSelectedTask] = useState<DelegatedTask | null>(null);
  const [taskDetailLoading, setTaskDetailLoading] = useState(false);
  const [taskDetailError, setTaskDetailError] = useState("");
  const defaultModel = models.find((item) => item.isDefault)?.model || "选择模型";
  const onlineConnections = connections.filter((item) => item.connected).length;
  const capabilities = [
    { label: "文档处理", prompt: "帮我解析并整理一份文档", route: "file_analysis", icon: <FileText size={18} /> },
    { label: "群聊摘要", prompt: "总结我离开期间的重要群聊消息", route: "chat_summary", icon: <UsersThree size={18} /> },
    { label: "日程规划", prompt: "根据最近消息规划今天的日程", route: "schedule_extract", icon: <CalendarDots size={18} /> },
    { label: "更多能力", prompt: "展示当前可以使用的能力", route: "", icon: <DotsThree size={18} /> },
  ];
  const terminalTaskStatuses = new Set(["COMPLETED", "FAILED", "CANCELLED"]);
  const visibleDelegatedTasks = delegatedTasks
    .filter((task) => taskListMode === "history" ? terminalTaskStatuses.has(task.status) : !terminalTaskStatuses.has(task.status))
    .filter((task) => task.id !== commandResult?.delegatedTask?.id)
    .slice(0, 8);

  /** 从服务端恢复最近委托任务，数据库异常只影响任务列表，不阻断首页其他能力。 */
  async function refreshDelegatedTasks() {
    setTasksLoading(true);
    setTasksError("");
    try {
      setDelegatedTasks(await onListTasks(30));
    } catch (error) {
      setTasksError(error instanceof Error ? error.message : "最近委托任务加载失败。");
    } finally {
      setTasksLoading(false);
    }
  }

  /** Dashboard 首次挂载时读取持久任务，客户端刷新或重启后不会丢失待办状态。 */
  useEffect(() => {
    void refreshDelegatedTasks();
    // 任务列表仅在 Dashboard 首次打开时自动读取，后续由明确操作触发刷新。
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  /** 合并服务端返回的任务状态，并同步可能正在展示的本次命令结果。 */
  function mergeDelegatedTask(updated: DelegatedTask) {
    setDelegatedTasks((current) => [updated, ...current.filter((task) => task.id !== updated.id)]);
    setCommandResult((current) => current?.delegatedTask?.id === updated.id
      ? { ...current, delegatedTask: updated, needConfirmation: updated.requiresConfirmation }
      : current);
  }

  /** 将能力快捷入口转换为可继续编辑的任务描述。 */
  function chooseCapability(nextPrompt: string, route: string) {
    setPrompt(nextPrompt);
    setRequestedRoute(route);
    setComposerNotice("");
    setCommandResult(null);
  }

  /** 切换工作模式时同步设置默认 Agent route，用户仍可继续修改具体任务描述。 */
  function chooseMode(nextMode: string) {
    setMode(nextMode);
    setRequestedRoute(nextMode === "inbox" ? "chat_summary" : nextMode === "work" ? "task_plan" : "");
    setCommandResult(null);
    setComposerNotice("");
  }

  /** 提交工作台命令并展示 Runtime 返回的真实 Agent 结果。 */
  async function submitPrompt() {
    if (!prompt.trim() || executing) return;
    setExecuting(true);
    setCommandResult(null);
    setComposerNotice("Agent 正在理解任务并选择工具…");
    try {
      const result = await onExecute(prompt.trim(), requestedRoute);
      setCommandResult(result);
      if (result.delegatedTask) mergeDelegatedTask(result.delegatedTask);
      setComposerNotice(result.status === "delegated" ? "已创建委托任务草案，请检查后确认。" : result.status === "success" ? "Agent 已完成本次任务。" : result.error || "Agent 执行失败。");
    } catch (error) {
      const message = error instanceof Error ? error.message : "Agent 执行失败。";
      setCommandResult({ commandId: "", status: "failed", route: requestedRoute, summary: "", finalReply: "", needConfirmation: false, results: [], delegatedTask: null, error: message });
      setComposerNotice(message);
    } finally {
      setExecuting(false);
    }
  }

  /** 在任务卡内控制委托任务，并用服务端最新状态替换本地缓存。 */
  async function updateDelegatedTask(task: DelegatedTask, action: "confirm" | DelegatedTaskControlAction) {
    if (!task || taskActionId) return;
    setTaskActionId(task.id);
    const actionLabels: Record<"confirm" | DelegatedTaskControlAction, string> = {
      confirm: "确认",
      pause: "暂停",
      resume: "继续",
      complete: "结束",
      cancel: "取消",
    };
    setComposerNotice(`正在${actionLabels[action]}委托任务…`);
    try {
      const updated = action === "confirm" ? await onConfirmTask(task.id) : await onControlTask(task.id, action);
      mergeDelegatedTask(updated);
      setSelectedTask((current) => current?.id === updated.id ? updated : current);
      setComposerNotice(`委托任务已${actionLabels[action]}。`);
    } catch (error) {
      setComposerNotice(error instanceof Error ? error.message : "任务状态更新失败。");
    } finally {
      setTaskActionId("");
    }
  }

  /** 打开任务详情时再读取服务端状态，避免首页持续轮询，同时保证时间线是最新的。 */
  async function openDelegatedTask(task: DelegatedTask) {
    setSelectedTask(task);
    setTaskDetailError("");
    setTaskDetailLoading(true);
    try {
      const updated = await onGetTask(task.id);
      mergeDelegatedTask(updated);
      setSelectedTask(updated);
    } catch (error) {
      setTaskDetailError(error instanceof Error ? error.message : "任务详情加载失败。");
    } finally {
      setTaskDetailLoading(false);
    }
  }

  return (
    <div className="assistant-home">
      <section className="assistant-intro">
        <div>
          <p className="eyebrow">PERSONAL AGENT WORKSPACE</p>
          <h1>把散落的信息，<br />整理成下一步。</h1>
          <p>连接聊天、文件、任务与日程。重要消息及时提醒，其余内容在合适的时间为你汇总。</p>
        </div>
        <span className="agent-ready"><Sparkle size={16} weight="fill" />Agent ready</span>
      </section>
      <div className="mode-tabs" role="tablist" aria-label="Agent 工作模式">
        <button className={mode === "assistant" ? "selected" : ""} onClick={() => chooseMode("assistant")}><Sparkle size={16} />日常助理</button>
        <button className={mode === "inbox" ? "selected" : ""} onClick={() => chooseMode("inbox")}><ChatCircleDots size={16} />消息整理</button>
        <button className={mode === "work" ? "selected" : ""} onClick={() => chooseMode("work")}><Brain size={16} />工作规划</button>
      </div>
      <div className="capability-row">
        {capabilities.map((item) => <button key={item.label} onClick={() => chooseCapability(item.prompt, item.route)}>{item.icon}{item.label}</button>)}
      </div>
      <section className="agent-composer">
        <textarea value={prompt} onChange={(event) => setPrompt(event.target.value)} aria-label="向 Memo Echo 输入任务" placeholder="告诉 Memo Echo 你想处理的事情…" />
        <div className="composer-footer">
          <div>
            <button type="button" onClick={onOpenModels}><Cpu size={16} />{defaultModel}</button>
            <button type="button" onClick={onOpenProfiles}><Sparkle size={16} />Skill</button>
            <button type="button" onClick={onOpenConnections}><ShieldCheck size={16} />默认权限</button>
          </div>
          <button className="send-button" disabled={!prompt.trim() || executing} onClick={() => void submitPrompt()} title="提交任务" aria-label="发送任务"><PaperPlaneRight size={18} weight="fill" /></button>
        </div>
      </section>
      <div className="environment-bar">
        <span><i className={onlineConnections > 0 ? "status-dot online-dot" : "status-dot"} />{onlineConnections}/{connections.length} 个平台在线</span>
        <span>{models.filter((item) => item.enabled).length} 个模型可用</span>
        <span className="environment-status" aria-live="polite">{composerNotice || status}</span>
      </div>
      {commandResult && <section className={`command-result ${commandResult.status === "failed" ? "command-failed" : "command-success"}`}><div className="command-result-heading"><span><Sparkle size={16} weight="fill" />{commandResult.status === "delegated" ? "委托任务草案" : commandResult.status === "success" ? "执行完成" : "执行失败"}</span><small>{commandResult.route || requestedRoute || "auto_route"}</small></div>{commandResult.delegatedTask ? <DelegatedTaskCard task={commandResult.delegatedTask} busy={taskActionId === commandResult.delegatedTask.id} onOpen={() => void openDelegatedTask(commandResult.delegatedTask!)} onAction={(action) => void updateDelegatedTask(commandResult.delegatedTask!, action)} /> : <><p>{commandResult.finalReply || commandResult.error || commandResult.summary || "Agent 没有返回可展示的文本结果。"}</p>{commandResult.results.length > 0 && <div className="command-agents">{commandResult.results.map((item, index) => <span key={`${item.agent}-${index}`}>{item.agent}<i>{item.status}</i></span>)}</div>}{commandResult.needConfirmation && <div className="command-warning"><ShieldCheck size={15} />本次结果需要你确认后才能执行外部操作。</div>}</>}</section>}
      <section className="delegated-task-list">
        <div className="delegated-task-list-heading">
          <div><p>DELEGATED TASKS</p><h2>委托任务</h2></div>
          <button type="button" disabled={tasksLoading} onClick={() => void refreshDelegatedTasks()}><ArrowClockwise size={15} />刷新</button>
        </div>
        <div className="delegated-task-filter" role="tablist" aria-label="委托任务分类">
          <button className={taskListMode === "active" ? "selected" : ""} type="button" onClick={() => setTaskListMode("active")}>进行中</button>
          <button className={taskListMode === "history" ? "selected" : ""} type="button" onClick={() => setTaskListMode("history")}>历史记录</button>
        </div>
        {tasksLoading && <p className="delegated-task-list-notice">正在恢复最近委托任务…</p>}
        {!tasksLoading && tasksError && <p className="delegated-task-list-notice error">{tasksError}</p>}
        {!tasksLoading && !tasksError && visibleDelegatedTasks.length === 0 && <p className="delegated-task-list-notice">{taskListMode === "active" ? "当前没有进行中的委托。" : "还没有已结束的委托。"}</p>}
        {!tasksLoading && !tasksError && visibleDelegatedTasks.length > 0 && <div className="delegated-task-list-grid">{visibleDelegatedTasks.map((task) => <DelegatedTaskCard key={task.id} task={task} busy={taskActionId === task.id} onOpen={() => void openDelegatedTask(task)} onAction={(action) => void updateDelegatedTask(task, action)} />)}</div>}
      </section>
      {selectedTask && <DelegatedTaskDialog task={selectedTask} loading={taskDetailLoading} error={taskDetailError} busy={taskActionId === selectedTask.id} onClose={() => setSelectedTask(null)} onAction={(action) => void updateDelegatedTask(selectedTask, action)} />}
    </div>
  );
}

type DelegatedTaskAction = "confirm" | DelegatedTaskControlAction;

type DelegatedTaskTimelineItem = {
  type?: string;
  eventType?: string;
  at?: string;
  speaker?: string;
  text?: string;
  command?: string;
};

type DelegatedTaskGraphState = {
  knownFacts: string[];
  pendingConditions: string[];
  lastEvidence: string[];
  timeline: DelegatedTaskTimelineItem[];
};

/** 将数据库中的 LangGraph JSON 转成只读详情；损坏数据不会阻断任务控制。 */
function parseDelegatedTaskGraphState(raw: string): DelegatedTaskGraphState {
  try {
    const value = JSON.parse(raw || "{}") as Partial<DelegatedTaskGraphState>;
    return {
      knownFacts: Array.isArray(value.knownFacts) ? value.knownFacts.map(String) : [],
      pendingConditions: Array.isArray(value.pendingConditions) ? value.pendingConditions.map(String) : [],
      lastEvidence: Array.isArray(value.lastEvidence) ? value.lastEvidence.map(String) : [],
      timeline: Array.isArray(value.timeline) ? value.timeline : [],
    };
  } catch {
    return { knownFacts: [], pendingConditions: [], lastEvidence: [], timeline: [] };
  }
}

/** 把内部状态码翻译成用户可理解的任务阶段。 */
function delegatedTaskStatusLabel(status: string) {
  const labels: Record<string, string> = {
    WAITING_TARGET: "等待选择联系人",
    WAITING_CONFIRMATION: "等待确认",
    READY: "准备执行",
    ACTIVE: "代理中",
    PAUSED: "已暂停",
    COMPLETED: "已完成",
    FAILED: "执行失败",
    CANCELLED: "已取消",
  };
  return labels[status] || status;
}

/**
 * 生成任务卡片的稳定标题。
 * 未完成目标绑定时不展示解析器的中间查询词，避免把“km预”一类残缺片段误认为联系人名称。
 */
function delegatedTaskDisplayTitle(task: DelegatedTask) {
  const resolvedName = (task.targetName || "").trim();
  if (resolvedName) return resolvedName;
  if (task.status === "WAITING_TARGET") return "等待选择联系人";
  return (task.targetQuery || "").trim() || "未指定会话";
}

/** 将 ISO 时间压缩为任务卡片和时间线共用的本地时间。 */
function formatDelegatedTaskTime(value: string | null | undefined) {
  if (!value) return "";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return value;
  return date.toLocaleString("zh-CN", { month: "numeric", day: "numeric", hour: "2-digit", minute: "2-digit" });
}

/** 根据任务状态集中定义允许展示的控制动作，避免前端发出非法状态转换。 */
function delegatedTaskActions(task: DelegatedTask): DelegatedTaskAction[] {
  if (task.status === "WAITING_CONFIRMATION") return ["confirm", "cancel"];
  if (task.status === "WAITING_TARGET" || task.status === "READY") return ["cancel"];
  if (task.status === "ACTIVE") return ["pause", "complete", "cancel"];
  if (task.status === "PAUSED") return ["resume", "complete", "cancel"];
  return [];
}

/** 用紧凑卡片展示目标、当前进度和合法控制动作。 */
function DelegatedTaskCard({ task, busy, onOpen, onAction }: { task: DelegatedTask; busy: boolean; onOpen: () => void; onAction: (action: DelegatedTaskAction) => void }) {
  const actions = delegatedTaskActions(task);
  const actionLabels: Record<DelegatedTaskAction, string> = { confirm: "确认", pause: "暂停", resume: "继续", complete: "结束", cancel: "取消" };
  return <article className="delegated-task-card">
    <div className="delegated-task-card-header">
      <div><strong>{delegatedTaskDisplayTitle(task)}</strong><small>{formatDelegatedTaskTime(task.updatedAt)}</small></div>
      <span className={`delegated-task-state state-${task.status.toLowerCase()}`}>{delegatedTaskStatusLabel(task.status)}</span>
    </div>
    <h3>{task.objective || task.originalCommand}</h3>
    <p className="delegated-task-progress">{task.progressSummary || task.clarificationQuestion || "等待 Agent 更新进度"}</p>
    <div className="delegated-task-meta"><span>{task.deadlineText || "未设置截止时间"}</span><span>{task.executionMode || "AUTO_COMPLETE"}</span></div>
    <div className="delegated-task-actions">
      <button type="button" disabled={busy} onClick={onOpen}>查看详情</button>
      {actions.map((action) => <button key={action} className={action === "confirm" || action === "resume" ? "primary" : ""} type="button" disabled={busy} onClick={() => onAction(action)}>{actionLabels[action]}</button>)}
    </div>
  </article>;
}

/** 展示持久化任务契约、可信事实和带时间戳的双方消息时间线。 */
function DelegatedTaskDialog({ task, loading, error, busy, onClose, onAction }: { task: DelegatedTask; loading: boolean; error: string; busy: boolean; onClose: () => void; onAction: (action: DelegatedTaskAction) => void }) {
  const graphState = parseDelegatedTaskGraphState(task.stateJson);
  const timeline = graphState.timeline.slice(-16);
  const actions = delegatedTaskActions(task);
  const actionLabels: Record<DelegatedTaskAction, string> = { confirm: "确认任务", pause: "暂停代理", resume: "继续代理", complete: "结束任务", cancel: "取消任务" };
  return <div className="delegated-task-dialog-backdrop" role="presentation" onMouseDown={(event) => { if (event.currentTarget === event.target) onClose(); }}>
    <section className="delegated-task-dialog" role="dialog" aria-modal="true" aria-label="委托任务详情">
      <header className="delegated-task-dialog-header">
        <div><p>DELEGATED TASK</p><h2>{delegatedTaskDisplayTitle(task)}</h2></div>
        <button type="button" onClick={onClose}>关闭</button>
      </header>
      {loading && <p className="delegated-task-list-notice">正在获取最新任务状态…</p>}
      {error && <p className="delegated-task-list-notice error">{error}</p>}
      <div className="delegated-task-dialog-summary">
        <span className={`delegated-task-state state-${task.status.toLowerCase()}`}>{delegatedTaskStatusLabel(task.status)}</span>
        <strong>{task.progressSummary || "等待 Agent 更新进度"}</strong>
        <small>更新于 {formatDelegatedTaskTime(task.updatedAt)}</small>
      </div>
      <div className="delegated-task-detail-grid">
        <div><span>任务目标</span><p>{task.objective || task.originalCommand}</p></div>
        <div><span>完成条件</span><p>{task.successCriteria || "等待对方明确回应"}</p></div>
        <div><span>时间要求</span><p>{task.deadlineText || "未设置"}</p></div>
        <div><span>执行模式</span><p>{task.executionMode || "AUTO_COMPLETE"}</p></div>
      </div>
      {(graphState.knownFacts.length > 0 || graphState.pendingConditions.length > 0) && <div className="delegated-task-state-columns">
        <div><h3>已确认事实</h3>{graphState.knownFacts.length > 0 ? <ul>{graphState.knownFacts.slice(-8).map((item) => <li key={item}>{item}</li>)}</ul> : <p>暂无</p>}</div>
        <div><h3>待满足条件</h3>{graphState.pendingConditions.length > 0 ? <ul>{graphState.pendingConditions.slice(-8).map((item) => <li key={item}>{item}</li>)}</ul> : <p>暂无</p>}</div>
      </div>}
      <div className="delegated-task-timeline-wrap">
        <h3>执行时间线</h3>
        {timeline.length === 0 ? <p className="delegated-task-list-notice">还没有可展示的执行事件。</p> : <ol className="delegated-task-timeline">{timeline.map((item, index) => <li key={`${item.at || "event"}-${index}`}>
          <span>{item.speaker || (item.type === "TASK_COMPILED" ? "任务" : "系统")}</span>
          <div><p>{item.text || item.command || item.eventType || item.type || "状态已更新"}</p><small>{formatDelegatedTaskTime(item.at)}</small></div>
        </li>)}</ol>}
      </div>
      {task.completionReport && <div className="delegated-task-completion"><span>结束报告</span><p>{task.completionReport}</p></div>}
      {actions.length > 0 && <footer className="delegated-task-dialog-actions">{actions.map((action) => <button key={action} className={action === "confirm" || action === "resume" ? "primary" : ""} type="button" disabled={busy} onClick={() => onAction(action)}>{actionLabels[action]}</button>)}</footer>}
    </section>
  </div>;
}

function LegacyDashboard({ connections, models, status }: { connections: PlatformConnection[]; models: ModelProfile[]; status: string }) {
  return <><div className="metrics"><article><span>平台连接</span><strong>{connections.length}</strong><small>{connections.filter((item) => item.connected).length} 个在线</small></article><article><span>模型配置</span><strong>{models.length}</strong><small>{models.filter((item) => item.enabled).length} 个启用</small></article><article><span>默认模型</span><strong>{models.find((item) => item.isDefault)?.model || "未设置"}</strong><small>用于智能处理</small></article></div><section className="panel"><div className="panel-title"><h2>接入状态</h2><span>{status}</span></div>{connections.length === 0 ? <p className="empty">尚未配置平台连接。请到“连接管理”接入 NapCat。</p> : connections.map((item) => <ConnectionRow key={item.id} item={item} />)}</section></>;
}

type ConversationProgressGroup = {
  key: string;
  name: string;
  platform: string;
  chatType: string;
  chatId: string;
  time: string;
  count: number;
  stage: string;
  topic: string;
  latestAction: string;
  attention: boolean;
};

type ConversationProgressCacheEntry = {
  summary: string;
  generatedByModel: boolean;
  generatedAt: string;
  latestAgentEventId: string;
};

/** 统一不同连接器对私聊类型的命名，避免 private/friend/direct 被拆成多个会话。 */
function normalizeConversationChatType(chatType: string) {
  const normalized = (chatType || "").trim().toLowerCase();
  if (["private", "friend", "direct", "dm"].includes(normalized)) return "private";
  if (["group", "channel", "room"].includes(normalized)) return "group";
  return normalized || "private";
}

/** 生成跨摘要批次、收件箱事件和会话列表都可复用的稳定会话键。 */
function buildConversationKey(platform: string, chatType: string, chatId: string) {
  return `${(platform || "unknown").trim().toLowerCase()}:${normalizeConversationChatType(chatType)}:${(chatId || "").trim()}`;
}

/** 为任务完成申请生成稳定的本地已读键，避免与普通收件箱事件 ID 冲突。 */
function buildTaskCompletionSeenId(item: ConversationProxyTaskState) {
  // 同一个会话可能在用户驳回后再次申请结束；时间戳用于让新申请重新显示未读提醒。
  return `task-completion:${item.profileId}:${item.chatId}:${item.requestedAt || item.updatedAt || "pending"}`;
}

/** 判断名称是否适合展示为联系人主标题，QQ 号和技术占位符只保留为辅助信息。 */
function isReadableConversationName(value: string | null | undefined, chatId: string) {
  const normalized = (value || "").trim();
  if (!normalized || normalized === chatId || /^\d{5,15}$/.test(normalized)) return false;
  return !/^(unknown|null|undefined|qq|私聊|群聊)$/i.test(normalized);
}

/**
 * 从真实入站发送者、会话摘要和工作台简报中恢复联系人名称。
 * 私聊优先选择 senderId 等于 chatId 的对方昵称，避免 Agent 代发事件把名称覆盖成自己或 QQ 号。
 */
function resolveConversationDisplayName(
  platform: string,
  chatType: string,
  chatId: string,
  conversations: ConversationSummary[],
  inboxItems: WorkspaceInboxItem[],
  briefing: WorkspaceBriefing | null,
) {
  const key = buildConversationKey(platform, chatType, chatId);
  const matchedConversation = conversations.find((item) => buildConversationKey(item.platform, item.chatType, item.chatId) === key);
  const matchedItems = inboxItems.filter((item) => buildConversationKey(item.platform, item.chatType, item.chatId) === key);
  const matchedBriefing = briefing?.importantConversations.find(
    (item) => buildConversationKey(item.platform, item.chatType, item.chatId) === key,
  );
  const normalizedType = normalizeConversationChatType(chatType);
  const peerSender = normalizedType === "private"
    ? matchedItems.find((item) => item.senderId?.trim() === chatId.trim() && isReadableConversationName(item.senderName, chatId))
    : undefined;
  const candidates = [
    peerSender?.senderName,
    matchedConversation?.chatName,
    matchedBriefing?.chatName,
    ...matchedItems.map((item) => item.chatName),
    matchedConversation?.lastSenderName,
    matchedBriefing?.lastSenderName,
    ...matchedItems.map((item) => item.senderName),
  ];
  return candidates.find((candidate) => isReadableConversationName(candidate, chatId))
    || (normalizedType === "group" ? "未命名群聊" : "QQ 联系人");
}

/** 清除模型和旧数据留下的 unknown 占位，并把纯附件消息转换成用户能理解的描述。 */
function cleanConversationText(value: string | null | undefined, fallback = "") {
  let text = (value || "").replace(/\s+/g, " ").trim();
  text = text.replace(/^(unknown|null|undefined)\s*[:：]\s*/i, "").trim();
  text = text.replace(/\[(?:非文本消息|non[- ]?text message)\]/gi, "收到一条非文本消息").trim();
  text = text.replace(/^(unknown|null|undefined)$/i, "").trim();
  return text || fallback;
}

/** 从当前登录用户的本地缓存读取最近一次手动查看结果；损坏数据直接忽略，不影响消息空间启动。 */
function readConversationProgressCache(storageKey: string): Record<string, ConversationProgressCacheEntry> {
  try {
    const value = window.localStorage.getItem(storageKey);
    return value ? JSON.parse(value) as Record<string, ConversationProgressCacheEntry> : {};
  } catch {
    return {};
  }
}

/** 保存摘要与最近 Agent 回复游标，不保存完整聊天内容，避免在客户端额外复制私密历史。 */
function writeConversationProgressCache(
  storageKey: string,
  cache: Record<string, ConversationProgressCacheEntry>,
) {
  try {
    window.localStorage.setItem(storageKey, JSON.stringify(cache));
  } catch {
    // 本地存储不可用时仍允许本次会话使用内存缓存。
  }
}

/**
 * 为消息空间的会话卡片生成统一头像。
 * QQ 私聊和群聊优先读取头像地址；网络不可用或图片失效时自动露出名称首字作为兜底。
 */
function ConversationListAvatar({ platform, chatType, chatId, senderId, name }: { platform: string; chatType: string; chatId: string; senderId?: string; name: string }) {
  const avatarUrl = resolveConversationAvatar(platform, chatType, chatId, senderId);
  return <span className="contact-avatar conversation-list-avatar">
    <i>{(name || "M").slice(0, 1)}</i>
    {avatarUrl && <img src={avatarUrl} alt="" onError={(event) => { event.currentTarget.style.display = "none"; }} />}
  </span>;
}

/** 根据平台与会话类型拼接头像地址，非 QQ 会话不猜测头像来源。 */
function resolveConversationAvatar(platform: string, chatType: string, chatId: string, senderId?: string) {
  if (platform.toLowerCase() !== "qq") return "";
  const numericId = (chatType === "group" ? chatId : (senderId || chatId)).trim();
  if (!/^\d{5,12}$/.test(numericId)) return "";
  return chatType === "group"
    ? `https://p.qlogo.cn/gh/${numericId}/${numericId}/100`
    : `https://q1.qlogo.cn/g?b=qq&nk=${numericId}&s=100`;
}

/** 将日程起止时间拆成表格需要的日期、开始时间和结束时间，避免一整段时间挤在同一行。 */
function formatScheduleDateTime(schedule: WorkspaceScheduleDigest) {
  if (!schedule.startTime) {
    return { dateLabel: "日期待定", startLabel: "待定", endLabel: "", relativeLabel: "" };
  }
  const start = new Date(schedule.startTime.replace(" ", "T"));
  if (Number.isNaN(start.getTime())) {
    return { dateLabel: "日期待定", startLabel: "待定", endLabel: "", relativeLabel: "" };
  }
  const end = schedule.endTime ? new Date(schedule.endTime.replace(" ", "T")) : null;
  const weekdays = ["周日", "周一", "周二", "周三", "周四", "周五", "周六"];
  const dateLabel = `${start.getMonth() + 1}月${start.getDate()}日 · ${weekdays[start.getDay()]}`;
  const startLabel = start.toLocaleTimeString("zh-CN", { hour: "2-digit", minute: "2-digit", hour12: false });
  const endLabel = end && !Number.isNaN(end.getTime())
    ? end.toLocaleTimeString("zh-CN", { hour: "2-digit", minute: "2-digit", hour12: false })
    : "";
  const today = new Date();
  today.setHours(0, 0, 0, 0);
  const scheduleDay = new Date(start);
  scheduleDay.setHours(0, 0, 0, 0);
  const dayOffset = Math.round((scheduleDay.getTime() - today.getTime()) / 86400000);
  const relativeLabel = dayOffset === 0 ? "今天" : dayOffset === 1 ? "明天" : dayOffset === 2 ? "后天" : "";
  return { dateLabel, startLabel, endLabel, relativeLabel };
}

/** 从日程来源字段和会话列表中恢复可读来源，QQ 会话优先显示真实群名或好友名。 */
function resolveScheduleSource(schedule: WorkspaceScheduleDigest, conversations: ConversationSummary[]) {
  const platform = (schedule.platform || "local").toLowerCase();
  const chatId = schedule.chatId || "";
  const inferredChatType = schedule.sourceEventId?.includes(":group:") ? "group" : "private";
  const conversation = conversations.find((item) => item.platform.toLowerCase() === platform && item.chatId === chatId);
  const chatType = conversation?.chatType || inferredChatType;
  const fallbackName = chatId
    ? `${chatType === "group" ? "群聊" : "会话"} ${chatId}`
    : "手动创建";
  return {
    platform,
    chatId,
    chatType,
    name: conversation?.chatName || fallbackName,
    meta: platform === "local" ? "本地日程" : `${platform.toUpperCase()} · ${chatType === "group" ? "群聊" : "私聊"}`,
  };
}

/** 仅在日程详情确实补充了标题之外的信息时展示，避免同一句事件内容重复两遍。 */
function resolveScheduleDetail(schedule: WorkspaceScheduleDigest) {
  const title = schedule.title?.trim() || "";
  const content = schedule.content?.trim() || "";
  if (!content || content === title || title.includes(content) || content.includes(title)) return "";
  return content;
}

/** 把日程来源接口转换为通用会话弹窗快照，同时保留真实消息而不生成虚构上下文。 */
function buildScheduleSourceSnapshot(context: WorkspaceScheduleSourceContext): ConversationProgressSnapshot {
  const sourceMessage = context.messages.find((message) => message.eventId === context.sourceEventId);
  const summary = context.sourceType === "manual"
    ? "该日程由你在 Memo Echo 客户端手动创建，没有关联聊天消息。"
    : context.sourceMessageFound
      ? `高亮消息是“${context.scheduleTitle}”的提取来源，窗口同时展示了这条消息前后的真实会话。`
      : "日程保留了来源事件标识，但当前消息存档中没有找到对应原文。";
  return {
    summary,
    generatedByModel: false,
    generatedAt: sourceMessage?.timestamp || "",
    summaryUpdated: false,
    latestAgentEventId: null,
    messages: context.messages,
  };
}

/** 按跨平台会话聚合消息，顶部四个入口分别切换摘要、代理进度、接管和日程。 */
function Messages({ cacheScope, conversations, inbox, taskCompletions, digests, briefing, status, memories, onRefresh, onHandoffsViewed, onSendHandoff, onFinishHandoff, onDecideTaskCompletion, onLoadGroupOperation, onApproveGroupOperation, onLoadConversationProgress, onLoadEventDetail, onViewMemoryEvidence, onCreateSchedule, onDeleteSchedule, onLoadScheduleSource, busy }: { cacheScope: string; conversations: ConversationSummary[]; inbox: WorkspaceInbox | null; taskCompletions: ConversationProxyTaskState[]; digests: ConversationDigestBatch[]; briefing: WorkspaceBriefing | null; status: string; memories: MemoryCandidate[]; onRefresh: () => Promise<void>; onHandoffsViewed: () => void; onSendHandoff: (eventId: string, message: string) => Promise<void>; onFinishHandoff: (item: WorkspaceInboxItem, continueAgent: boolean) => Promise<void>; onDecideTaskCompletion: (item: ConversationProxyTaskState, approved: boolean) => Promise<void>; onLoadGroupOperation: (eventId: string) => Promise<PendingGroupOperation>; onApproveGroupOperation: (eventId: string, confirmationText: string) => Promise<void>; onLoadConversationProgress: (platform: string, chatType: string, chatId: string, lastSeenAgentEventId?: string) => Promise<ConversationProgressSnapshot>; onLoadEventDetail: (eventId: string) => Promise<StoredEventDetail>; onViewMemoryEvidence: (memory: MemoryCandidate) => void; onCreateSchedule: (draft: WorkspaceScheduleDraft) => Promise<void>; onDeleteSchedule: (scheduleId: string) => Promise<void>; onLoadScheduleSource: (scheduleId: string) => Promise<WorkspaceScheduleSourceContext>; busy: boolean }) {
  const inboxItems = inbox?.items || [];
  const handoffs = inboxItems.filter((item) => item.needHumanConfirmation);
  const handoffCount = handoffs.length + taskCompletions.length;
  /**
   * 已回复数量按会话统计，而不是按事件统计。
   * 会话摘要会保留最后一次处理状态，因此旧收件箱事件归档后仍能显示代理进度。
   */
  const repliedConversationCount = new Set([
    ...inboxItems
      .filter((item) => item.writeBackStatus === "SENT" || item.processingStatus === "AUTO_REPLIED")
      .map((item) => buildConversationKey(item.platform, item.chatType, item.chatId)),
    ...conversations
      .filter((item) => ["SENT", "DELAYED_SENT"].includes(item.lastWriteBackStatus || "")
        || item.lastProcessingStatus === "AUTO_REPLIED")
      .map((item) => buildConversationKey(item.platform, item.chatType, item.chatId)),
  ]).size;
  // 近期日程优先使用新接口；旧版服务仍可回退到今日日程，避免客户端升级期间白屏。
  const schedules = briefing?.upcomingSchedules ?? briefing?.todaySchedules ?? [];
  const [activeSection, setActiveSection] = useState<"digests" | "progress" | "handoffs" | "schedules">("digests");
  const [handoffReplies, setHandoffReplies] = useState<Record<string, string>>({});
  const [sentHandoffs, setSentHandoffs] = useState<Record<string, boolean>>({});
  const [handoffBusy, setHandoffBusy] = useState("");
  const [handoffError, setHandoffError] = useState("");
  const [taskDecisionBusy, setTaskDecisionBusy] = useState("");
  const [taskDecisionError, setTaskDecisionError] = useState("");
  const [taskDecisionErrorKey, setTaskDecisionErrorKey] = useState("");
  const [contextTarget, setContextTarget] = useState<ConversationProgressGroup | null>(null);
  const [contextSnapshot, setContextSnapshot] = useState<ConversationProgressSnapshot | null>(null);
  const [contextLoading, setContextLoading] = useState(false);
  const [contextError, setContextError] = useState("");
  const [scheduleEditorOpen, setScheduleEditorOpen] = useState(false);
  const [scheduleActionError, setScheduleActionError] = useState("");
  const [deletingScheduleId, setDeletingScheduleId] = useState("");
  const [scheduleSourceTarget, setScheduleSourceTarget] = useState<WorkspaceScheduleDigest | null>(null);
  const [scheduleSourceContext, setScheduleSourceContext] = useState<WorkspaceScheduleSourceContext | null>(null);
  const [scheduleSourceLoading, setScheduleSourceLoading] = useState(false);
  const [scheduleSourceError, setScheduleSourceError] = useState("");
  const [memoryAuditEventId, setMemoryAuditEventId] = useState("");
  const [memoryAuditIds, setMemoryAuditIds] = useState<string[]>([]);
  const [memoryAuditLoading, setMemoryAuditLoading] = useState(false);
  const [memoryAuditError, setMemoryAuditError] = useState("");
  const cacheStorageKey = `memo-echo:conversation-progress:${cacheScope}`;
  const [progressCache, setProgressCache] = useState<Record<string, ConversationProgressCacheEntry>>(
    () => readConversationProgressCache(cacheStorageKey),
  );
  // 请求序号用于丢弃用户关闭弹窗后才返回的旧响应，避免覆盖下一次打开的会话。
  const contextRequestId = useRef(0);
  const scheduleSourceRequestId = useRef(0);
  const memoryAuditRequestId = useRef(0);

  /** 按事件读取执行轨迹，并且只采用服务端明确记录的已确认记忆 ID。 */
  async function openMemoryAudit(eventId: string) {
    const requestId = ++memoryAuditRequestId.current;
    setMemoryAuditEventId(eventId);
    setMemoryAuditIds([]);
    setMemoryAuditError("");
    setMemoryAuditLoading(true);
    try {
      const detail = await onLoadEventDetail(eventId);
      if (memoryAuditRequestId.current !== requestId) return;
      setMemoryAuditIds(detail.executionTrace?.verifiedMemoryIds || []);
    } catch (error) {
      if (memoryAuditRequestId.current !== requestId) return;
      setMemoryAuditError(error instanceof Error ? error.message : "执行记忆审计读取失败");
    } finally {
      if (memoryAuditRequestId.current === requestId) setMemoryAuditLoading(false);
    }
  }

  /** 关闭执行审计并让尚未返回的旧请求失效，避免结果串到下一条事件。 */
  function closeMemoryAudit() {
    memoryAuditRequestId.current += 1;
    setMemoryAuditEventId("");
    setMemoryAuditIds([]);
    setMemoryAuditLoading(false);
    setMemoryAuditError("");
  }

  /** 从执行审计下钻到原始聊天证据时先关闭当前弹窗，避免两层遮罩互相覆盖。 */
  function viewMemoryEvidence(memory: MemoryCandidate) {
    closeMemoryAudit();
    onViewMemoryEvidence(memory);
  }

  /** 发送人工编辑内容，并在成功后切换到是否继续代理的决策阶段。 */
  async function sendHandoff(item: WorkspaceInboxItem) {
    const message = (handoffReplies[item.eventId] || "").trim();
    if (!message) { setHandoffError("请先填写要发送的内容。"); return; }
    setHandoffBusy(item.eventId); setHandoffError("");
    try {
      await onSendHandoff(item.eventId, message);
      setSentHandoffs((current) => ({ ...current, [item.eventId]: true }));
    } catch (error) {
      setHandoffError(error instanceof Error ? error.message : "人工回复发送失败");
    } finally { setHandoffBusy(""); }
  }
  /** 切换消息空间分页；进入接管页时才将当前红点标记为已查看。 */
  function selectSection(section: typeof activeSection) {
    setActiveSection(section);
    if (section === "handoffs") onHandoffsViewed();
  }

  /** 提交任务结束决定，并在请求期间只锁定当前卡片，其他接管事项仍可处理。 */
  async function decideTaskCompletion(item: ConversationProxyTaskState, approved: boolean) {
    const key = buildTaskCompletionSeenId(item);
    setTaskDecisionBusy(key);
    setTaskDecisionError("");
    setTaskDecisionErrorKey("");
    try {
      await onDecideTaskCompletion(item, approved);
    } catch (error) {
      setTaskDecisionError(error instanceof Error ? error.message : "任务完成申请处理失败");
      setTaskDecisionErrorKey(key);
    } finally {
      setTaskDecisionBusy("");
    }
  }

  /**
   * 打开会话上下文后读取最新时间线；只有发现上次查看后出现新的 Agent 回复，后端才调用模型更新进度。
   * 摘要和 Agent 游标按登录用户及会话保存在本机，重启客户端后也不会重复消耗模型额度。
   */
  async function openConversationContext(group: ConversationProgressGroup) {
    const requestId = ++contextRequestId.current;
    setContextTarget(group);
    setContextSnapshot(null);
    setContextError("");
    setContextLoading(true);
    try {
      const cached = progressCache[group.key];
      const snapshot = await onLoadConversationProgress(
        group.platform,
        group.chatType,
        group.chatId,
        cached?.latestAgentEventId || "",
      );
      if (contextRequestId.current !== requestId) return;
      const effectiveSummary = snapshot.summaryUpdated || !cached?.summary
        ? snapshot.summary
        : cached.summary;
      const effectiveSnapshot = {
        ...snapshot,
        summary: effectiveSummary,
        generatedByModel: snapshot.summaryUpdated ? snapshot.generatedByModel : Boolean(cached?.generatedByModel),
        generatedAt: snapshot.summaryUpdated ? snapshot.generatedAt : (cached?.generatedAt || snapshot.generatedAt),
      };
      const cacheEntry: ConversationProgressCacheEntry = {
        summary: effectiveSummary,
        generatedByModel: effectiveSnapshot.generatedByModel,
        generatedAt: effectiveSnapshot.generatedAt,
        latestAgentEventId: snapshot.latestAgentEventId || cached?.latestAgentEventId || "",
      };
      setContextSnapshot(effectiveSnapshot);
      setProgressCache((current) => {
        const next = { ...current, [group.key]: cacheEntry };
        writeConversationProgressCache(cacheStorageKey, next);
        return next;
      });
    } catch (error) {
      if (contextRequestId.current !== requestId) return;
      setContextError(error instanceof Error ? error.message : "当前聊天进度获取失败");
    } finally {
      if (contextRequestId.current === requestId) setContextLoading(false);
    }
  }

  /** 关闭上下文弹窗但保留本次概括，使卡片可以继续展示最近一次手动获取结果。 */
  function closeConversationContext() {
    contextRequestId.current += 1;
    setContextTarget(null);
    setContextSnapshot(null);
    setContextError("");
    setContextLoading(false);
  }

  /** 删除前要求用户确认，成功后由父组件刷新工作台简报。 */
  async function deleteSchedule(schedule: WorkspaceScheduleDigest) {
    if (!window.confirm(`确定删除“${schedule.title || "未命名日程"}”吗？`)) return;
    setDeletingScheduleId(schedule.id);
    setScheduleActionError("");
    try {
      await onDeleteSchedule(schedule.id);
    } catch (error) {
      setScheduleActionError(error instanceof Error ? error.message : "日程删除失败");
    } finally {
      setDeletingScheduleId("");
    }
  }

  /** 打开日程来源弹窗并按需读取原始消息附近的会话片段。 */
  async function openScheduleSource(schedule: WorkspaceScheduleDigest) {
    const requestId = ++scheduleSourceRequestId.current;
    setScheduleSourceTarget(schedule);
    setScheduleSourceContext(null);
    setScheduleSourceError("");
    setScheduleSourceLoading(true);
    try {
      const context = await onLoadScheduleSource(schedule.id);
      if (scheduleSourceRequestId.current === requestId) setScheduleSourceContext(context);
    } catch (error) {
      if (scheduleSourceRequestId.current === requestId) {
        setScheduleSourceError(error instanceof Error ? error.message : "日程来源读取失败");
      }
    } finally {
      if (scheduleSourceRequestId.current === requestId) setScheduleSourceLoading(false);
    }
  }

  /** 关闭日程来源弹窗并使尚未返回的旧请求失效。 */
  function closeScheduleSource() {
    scheduleSourceRequestId.current += 1;
    setScheduleSourceTarget(null);
    setScheduleSourceContext(null);
    setScheduleSourceError("");
    setScheduleSourceLoading(false);
  }

  /**
   * 把多个慢通道批次合并成一个会话摘要视图。
   * 数据库仍保留每次不可变摘要用于审计，客户端只展示每个会话最新的一条，避免同一联系人刷屏。
   */
  const digestGroups = Array.from(digests.reduce((groups, digest) => {
    const key = buildConversationKey(digest.platform, digest.chatType, digest.chatId);
    const current = groups.get(key) || [];
    current.push(digest);
    groups.set(key, current);
    return groups;
  }, new Map<string, ConversationDigestBatch[]>()).entries()).map(([key, batches]) => {
    const ordered = [...batches].sort((left, right) => Date.parse(right.generatedAt) - Date.parse(left.generatedAt));
    const latest = ordered[0];
    const related = inboxItems.filter((item) => buildConversationKey(item.platform, item.chatType, item.chatId) === key);
    const happened = ordered
      .map((item) => cleanConversationText(item.happened || item.summary))
      .find(Boolean) || "最近收到一条非文本消息，打开上下文可查看原始内容";
    return {
      ...latest,
      key,
      displayName: resolveConversationDisplayName(
        latest.platform, latest.chatType, latest.chatId, conversations, inboxItems, briefing,
      ),
      happened,
      actionItems: cleanConversationText(latest.actionItems, "暂时没有需要你立即处理的事项"),
      nextStep: cleanConversationText(latest.nextStep, "有空时打开上下文快速确认即可"),
      totalMessageCount: ordered.reduce((total, item) => total + Math.max(0, item.messageCount), 0),
      urgentCount: related.filter((item) => item.actionRequired).length,
      hasDraft: related.some((item) => Boolean(item.replyDraft)),
    };
  }).sort((left, right) => Date.parse(right.generatedAt) - Date.parse(left.generatedAt));

  /** 将收件箱事件按规范化会话键聚合，保证每个会话在代理进度里最多占一行。 */
  const inboxGroups = inboxItems.reduce((groups, item) => {
    const key = buildConversationKey(item.platform, item.chatType, item.chatId);
    const current = groups.get(key) || [];
    current.push(item);
    groups.set(key, current);
    return groups;
  }, new Map<string, WorkspaceInboxItem[]>());

  /**
   * 代理进度同时参考收件箱和会话摘要。
   * 即使旧事件已从收件箱完成归档，只要会话记录仍在，也能展示最近状态并允许用户按需获取自然语言进度。
   */
  const progressKeys = new Set<string>(inboxGroups.keys());
  conversations.forEach((conversation) => {
    if (conversation.lastProcessingStatus || conversation.lastWriteBackStatus) {
      progressKeys.add(buildConversationKey(conversation.platform, conversation.chatType, conversation.chatId));
    }
  });
  const progressGroups = Array.from(progressKeys).map((key) => {
    const items = [...(inboxGroups.get(key) || [])]
      .sort((left, right) => Date.parse(right.timestamp) - Date.parse(left.timestamp));
    const conversation = conversations.find(
      (item) => buildConversationKey(item.platform, item.chatType, item.chatId) === key,
    );
    const digest = digestGroups.find((item) => item.key === key);
    const latest = items[0];
    const [platform, chatType, ...chatIdParts] = key.split(":");
    const chatId = conversation?.chatId || latest?.chatId || digest?.chatId || chatIdParts.join(":");
    const processingStatus = latest?.processingStatus || conversation?.lastProcessingStatus || "";
    const writeBackStatus = latest?.writeBackStatus || conversation?.lastWriteBackStatus || "";
    const sent = items.some((item) => item.writeBackStatus === "SENT" || item.processingStatus === "AUTO_REPLIED")
      || ["SENT", "DELAYED_SENT"].includes(writeBackStatus)
      || processingStatus === "AUTO_REPLIED";
    const waiting = items.some((item) => item.needHumanConfirmation);
    const failed = items.some((item) => item.processingStatus.includes("FAILED") || item.writeBackStatus === "FAILED")
      || processingStatus.includes("FAILED") || writeBackStatus === "FAILED";
    const recentText = items.map((item) => cleanConversationText(item.text)).find(Boolean) || "";
    const stage = waiting ? "等待你接管" : failed ? "处理遇到异常" : sent ? "Agent 正在持续代理" : "已分析，等待新消息";
    const defaultProgress = waiting
      ? "自动回复已暂停，当前等待你查看上下文并决定如何继续"
      : failed
        ? "最近一次处理遇到异常，打开上下文可重新获取当前会话进度"
        : sent
          ? "最近一轮已经回复完成，打开上下文可生成最新的对话走向总结"
          : "尚未生成会话进度，打开上下文后会根据双方消息进行分析";
    return {
      key,
      name: resolveConversationDisplayName(platform, chatType, chatId, conversations, inboxItems, briefing),
      platform,
      chatType,
      chatId,
      time: latest?.timestamp || conversation?.lastMessageTime || digest?.generatedAt || "",
      count: items.length || digest?.totalMessageCount || 0,
      stage,
      topic: digest?.happened
        || (recentText ? `最近围绕“${compactProgressText(recentText)}”展开` : "最近收到非文本内容，建议打开上下文查看原消息"),
      latestAction: progressCache[key]?.summary || defaultProgress,
      attention: Boolean(waiting || failed),
    };
  }).sort((left, right) => Date.parse(right.time) - Date.parse(left.time)) as ConversationProgressGroup[];

  return <div className="inbox-page message-space-v2">
    <section className="inbox-heading"><div><p className="eyebrow">PERSONAL INFORMATION HUB</p><h2>消息空间</h2><p>近期发生的事、Agent 代理进度和接下来的安排，都汇总在这里。</p></div><button className="outline-button" onClick={() => void onRefresh()} disabled={busy}><ArrowClockwise size={17} className={busy ? "spin" : ""} />刷新</button></section>
    <nav className="message-section-tabs" aria-label="消息空间分类">
      <button className={activeSection === "digests" ? "active" : ""} onClick={() => selectSection("digests")}><small>近期摘要</small><b>{digestGroups.length}</b></button>
      <button className={activeSection === "progress" ? "active" : ""} onClick={() => selectSection("progress")}><small>Agent 已回复</small><b>{repliedConversationCount}</b></button>
      <button className={`${activeSection === "handoffs" ? "active" : ""} ${handoffCount ? "attention" : ""}`} onClick={() => selectSection("handoffs")}><small>等待接管</small><b>{handoffCount}</b></button>
      <button className={activeSection === "schedules" ? "active" : ""} onClick={() => selectSection("schedules")}><small>近期日程</small><b>{schedules.length}</b></button>
    </nav>

    {activeSection === "handoffs" && <section className="handoff-board message-section-panel">
      <div className="digest-board-title"><b>需要你确认</b><span>{handoffCount ? `${handoffCount} 个会话等待确认` : "当前没有待确认会话"}</span></div>
      {handoffCount === 0 ? <div className="section-empty"><ShieldCheck size={25} /><b>没有需要确认的对话</b><span>严格审查拦截或 Agent 申请结束代理的会话会出现在这里。</span></div> : <>
      {taskCompletions.map((item) => {
        const decisionKey = buildTaskCompletionSeenId(item);
        return <article className="task-completion-card" key={decisionKey}>
          <header><div><ConversationListAvatar platform={item.platform} chatType={item.chatType} chatId={item.chatId} name={item.profileName || item.chatId} /><div><b>{item.profileName || item.chatId}</b><small>{item.platform.toUpperCase()} · {formatTime(item.requestedAt || item.updatedAt || "")}</small></div></div><span className="task-completion-badge">申请结束代理</span></header>
          <div className="task-completion-summary"><b>{item.completionSummary || "Agent 判断本次会话任务已经完成"}</b>{item.completionReason && <p>{item.completionReason}</p>}</div>
          {item.completionEvidence.length > 0 && <div className="task-completion-evidence"><small>完成依据</small>{item.completionEvidence.slice(0, 3).map((evidence, index) => <p key={`${decisionKey}:${index}`}>{evidence}</p>)}</div>}
          <div className="task-completion-actions"><div><b>是否结束这个会话的 Agent 代理？</b><small>确认后停止代理；选择继续时会保留现有进度，不会从头重复任务。</small></div><button className="secondary" type="button" disabled={taskDecisionBusy === decisionKey} onClick={() => void decideTaskCompletion(item, false)}>任务仍需继续</button><button type="button" disabled={taskDecisionBusy === decisionKey} onClick={() => void decideTaskCompletion(item, true)}>{taskDecisionBusy === decisionKey ? "处理中" : "确认结束代理"}</button></div>
          {taskDecisionError && taskDecisionErrorKey === decisionKey && <footer className="task-completion-error" role="alert">{taskDecisionError}</footer>}
        </article>;
      })}
      {handoffs.map((item) => <article key={item.eventId}>
        <header><div><ConversationListAvatar platform={item.platform} chatType={item.chatType} chatId={item.chatId} senderId={item.senderId} name={item.chatName || item.senderName || item.chatId} /><div><b>{item.chatName || item.senderName || item.chatId}</b><small>{item.platform.toUpperCase()} · {formatTime(item.timestamp)}</small></div></div><span className="digest-urgent">{item.route === "group_ops" ? "群管理待审批" : "已停止自动回复"}</span></header>
        <p className="handoff-summary">{item.replyDraft || "审批 Agent 要求你亲自确认当前操作。"}</p>
        <div className="memory-audit-action">
          <button type="button" onClick={() => void openMemoryAudit(item.eventId)}><Brain size={15} />查看记忆依据</button>
        </div>
        {item.route === "group_ops"
          ? <GroupOperationApprovalPanel item={item} onLoad={onLoadGroupOperation} onApprove={onApproveGroupOperation} />
          : !sentHandoffs[item.eventId]
            ? <div className="handoff-compose"><textarea value={handoffReplies[item.eventId] || ""} onChange={(event) => setHandoffReplies((current) => ({ ...current, [item.eventId]: event.target.value }))} placeholder="在这里输入你要亲自发送的回复" /><button type="button" disabled={handoffBusy === item.eventId} onClick={() => void sendHandoff(item)}><PaperPlaneRight size={16} />{handoffBusy === item.eventId ? "发送中" : "发送回复"}</button></div>
            : <div className="handoff-next"><b>回复已发送，是否继续让 Agent 代理这个会话？</b><div><button type="button" onClick={() => void onFinishHandoff(item, true)}>继续 Agent 代理</button><button className="secondary" type="button" onClick={() => void onFinishHandoff(item, false)}>暂停该会话代理</button></div></div>}
        {item.route !== "group_ops" && <footer>{handoffError && handoffBusy !== item.eventId ? handoffError : "摘要仅供你恢复上下文，不会自动发送。"}</footer>}
      </article>)}
      </>}
    </section>}

    {activeSection === "digests" && <section className="digest-board compact-digest-board message-section-panel"><div className="digest-board-title"><b>近期摘要</b><span>{status}</span></div>{digestGroups.length === 0 ? <div className="inbox-empty compact-empty"><ChatCircleDots size={24} /><h3>暂时没有新摘要</h3><p>达到消息数量或等待时间阈值后会自动生成。</p></div> : digestGroups.slice(0, 8).map((digest) => {
      return <article className="digest-card digest-card-compact conversation-digest-row" key={digest.key}>
        <header>
          <div>
            <ConversationListAvatar platform={digest.platform} chatType={digest.chatType} chatId={digest.chatId} name={digest.displayName} />
            <div><b>{digest.displayName}</b><small>{digest.platform.toUpperCase()} · {normalizeConversationChatType(digest.chatType) === "group" ? "群聊" : `私聊 · ${digest.chatId}`} · {formatTime(digest.generatedAt)}</small></div>
          </div>
          {digest.urgentCount > 0 && <span className="digest-urgent">{digest.urgentCount} 项需处理</span>}
        </header>
        <div className="conversation-digest-content">
          <section><small>最近发生</small><p>{digest.happened}</p></section>
          <div className="digest-inline-actions"><span><b>待办</b>{digest.actionItems}</span><span><b>下一步</b>{digest.nextStep}</span></div>
        </div>
        <footer><span>合并 {digest.totalMessageCount} 条消息</span>{digest.hasDraft && <span>已生成草稿</span>}</footer>
      </article>;
    })}</section>}

    {activeSection === "progress" && <section className="conversation-progress-board message-section-panel"><div className="digest-board-title"><b>Agent 代理进度</b><span>打开会话时按需更新自然语言进度</span></div>{progressGroups.length === 0 ? <div className="section-empty"><Brain size={25} /><b>还没有代理进度</b><span>Agent 开始处理会话后，这里会按会话汇总聊天走向。</span></div> : <div className="conversation-progress-list">{progressGroups.map((group) => <article key={group.key}><header><div><ConversationListAvatar platform={group.platform} chatType={group.chatType} chatId={group.chatId} name={group.name} /><div><b>{group.name}</b><small>{group.platform.toUpperCase()} · {normalizeConversationChatType(group.chatType) === "group" ? "群聊" : `私聊 · ${group.chatId}`} · {formatTime(group.time)} · 聚合 {group.count} 条</small></div></div><span className={group.attention ? "progress-stage attention" : "progress-stage"}>{group.stage}</span></header><div className="progress-story"><section><small>对话概况</small><p>{group.topic}</p></section><section><small>当前进度</small><p>{group.latestAction}</p></section><div className="progress-context-action"><button type="button" onClick={() => void openConversationContext(group)}><ChatCircleDots size={16} />查看上下文</button><small>打开时获取最新进度</small></div></div></article>)}</div>}</section>}

    {activeSection === "schedules" && <section className="schedule-summary-board message-section-panel">
      <div className="digest-board-title"><b>近期日程</b><div className="schedule-board-actions"><span>{`${schedules.length} 项安排`}</span><button type="button" onClick={() => { setScheduleActionError(""); setScheduleEditorOpen(true); }}><Plus size={14} />手动添加</button></div></div>
      {scheduleActionError && <p className="schedule-action-error" role="alert">{scheduleActionError}</p>}
      {schedules.length === 0 ? <div className="section-empty"><CalendarDots size={25} /><b>近期还没有已同步的日程</b><span>从通知中提取或手动创建的未来日程会统一显示在这里。</span></div> : <div className="schedule-table" role="table" aria-label="近期日程">
        <div className="schedule-table-head" role="row">
          <span role="columnheader">时间</span>
          <span role="columnheader">地点</span>
          <span role="columnheader">事件</span>
          <span role="columnheader">来源</span>
        </div>
        <div className="schedule-table-body">
          {schedules.map((schedule) => {
            const scheduleTime = formatScheduleDateTime(schedule);
            const source = resolveScheduleSource(schedule, conversations);
            const detail = resolveScheduleDetail(schedule);
            const timeMeta = [scheduleTime.relativeLabel, scheduleTime.endLabel ? `至 ${scheduleTime.endLabel}` : ""].filter(Boolean).join(" · ");
            return <article className="schedule-table-row" role="row" key={schedule.id}>
              <time className="schedule-time-cell" role="cell" data-label="时间" dateTime={schedule.startTime || undefined}>
                <span>{scheduleTime.dateLabel}</span>
                <strong>{scheduleTime.startLabel}</strong>
                <small>{timeMeta || "时间已确认"}</small>
              </time>
              <div className="schedule-location-cell" role="cell" data-label="地点">
                <MapPinLine size={16} weight="duotone" />
                <span>{schedule.location || "未设置地点"}</span>
              </div>
              <div className="schedule-event-cell" role="cell" data-label="事件">
                <i aria-hidden="true" />
                <div className="schedule-event-content"><div><b>{schedule.title || "未命名日程"}</b>{detail && <p>{detail}</p>}</div><button type="button" title="删除日程" aria-label={`删除日程 ${schedule.title || "未命名日程"}`} disabled={deletingScheduleId === schedule.id} onClick={() => void deleteSchedule(schedule)}><Trash size={15} />{deletingScheduleId === schedule.id && <span>删除中</span>}</button></div>
              </div>
              <div className="schedule-source-cell" role="cell" data-label="来源">
                <button type="button" className="schedule-source-button" onClick={() => void openScheduleSource(schedule)}>
                  <ConversationListAvatar platform={source.platform} chatType={source.chatType} chatId={source.chatId} senderId={schedule.senderId || undefined} name={source.name} />
                  <div><b>{source.name}</b><small>{source.meta} · 查看原消息</small></div>
                  <ArrowSquareOut size={14} />
                </button>
              </div>
            </article>;
          })}
        </div>
      </div>}
    </section>}
    <MemoryAuditDialog
      open={Boolean(memoryAuditEventId)}
      eventId={memoryAuditEventId}
      memoryIds={memoryAuditIds}
      memories={memories}
      loading={memoryAuditLoading}
      error={memoryAuditError}
      onClose={closeMemoryAudit}
      onRetry={() => memoryAuditEventId && void openMemoryAudit(memoryAuditEventId)}
      onViewEvidence={viewMemoryEvidence}
    />
    <ConversationContextDialog
      open={Boolean(contextTarget)}
      contactName={contextTarget?.name || "会话"}
      platform={contextTarget?.platform || ""}
      snapshot={contextSnapshot}
      loading={contextLoading}
      error={contextError}
      onClose={closeConversationContext}
      onRetry={() => contextTarget && void openConversationContext(contextTarget)}
    />
    <ConversationContextDialog
      open={Boolean(scheduleSourceTarget)}
      contactName={scheduleSourceContext?.chatName || scheduleSourceTarget?.title || "日程来源"}
      platform={scheduleSourceContext?.platform || scheduleSourceTarget?.platform || "local"}
      snapshot={scheduleSourceContext ? buildScheduleSourceSnapshot(scheduleSourceContext) : null}
      loading={scheduleSourceLoading}
      error={scheduleSourceError}
      headerMeta="日程来源上下文"
      summaryTitle="来源说明"
      summaryBadge={scheduleSourceContext?.sourceType === "manual" ? "手动创建" : "原始会话"}
      loadingTitle="正在读取日程来源"
      loadingDescription="正在定位原始消息，并读取这条消息前后的真实会话片段"
      emptyText={scheduleSourceContext?.sourceType === "manual" ? "手动日程没有关联聊天消息" : "当前没有可展示的来源消息"}
      highlightEventId={scheduleSourceContext?.sourceEventId || ""}
      onClose={closeScheduleSource}
      onRetry={() => scheduleSourceTarget && void openScheduleSource(scheduleSourceTarget)}
    />
    <ScheduleEditorDialog open={scheduleEditorOpen} onClose={() => setScheduleEditorOpen(false)} onSubmit={onCreateSchedule} />
  </div>;
}

const GROUP_OPERATION_LABELS: Record<string, string> = {
  mute_member: "禁言群成员",
  unmute_member: "解除成员禁言",
  whole_mute: "切换全员禁言",
  set_member_card: "修改群名片",
  set_group_name: "修改群名称",
  publish_notice: "发布群公告",
  set_essence: "设为精华消息",
  delete_essence: "移除精华消息",
  kick_member: "移出群成员",
  set_admin: "修改群管理员",
};

/**
 * 渲染受控群管理审批单。客户端只能提交后端给出的确认短语，不能修改动作参数，
 * 因此 UI 被篡改也无法把一个低风险提案替换成另一项 NapCat 操作。
 */
function GroupOperationApprovalPanel({ item, onLoad, onApprove }: { item: WorkspaceInboxItem; onLoad: (eventId: string) => Promise<PendingGroupOperation>; onApprove: (eventId: string, confirmationText: string) => Promise<void> }) {
  const [approval, setApproval] = useState<PendingGroupOperation | null>(null);
  const [confirmationText, setConfirmationText] = useState("");
  const [loading, setLoading] = useState(false);
  const [executing, setExecuting] = useState(false);
  const [error, setError] = useState("");

  /** 按需读取不含执行令牌的安全审批摘要，避免把 Runtime 凭据暴露给桌面端。 */
  async function loadApproval() {
    setLoading(true);
    setError("");
    try {
      setApproval(await onLoad(item.eventId));
    } catch (loadError) {
      setError(loadError instanceof Error ? loadError.message : "群管理审批单读取失败");
    } finally {
      setLoading(false);
    }
  }

  /** 提交人工输入的确认短语；真正的动作、参数、有效期和一次性令牌均由服务端校验。 */
  async function approve() {
    if (!approval || !confirmationText.trim()) {
      setError("请先按要求输入确认短语");
      return;
    }
    setExecuting(true);
    setError("");
    try {
      await onApprove(item.eventId, confirmationText.trim());
    } catch (approvalError) {
      setError(approvalError instanceof Error ? approvalError.message : "群管理操作执行失败");
    } finally {
      setExecuting(false);
    }
  }

  if (!approval) {
    return <div className="group-operation-gate">
      <div><ShieldCheck size={18} /><span><b>受控执行</b><small>参数已冻结，读取审批单后仍需明确确认</small></span></div>
      <button type="button" disabled={loading} onClick={() => void loadApproval()}>{loading ? "读取中…" : "查看并审批"}</button>
      {error && <p role="alert">{error}</p>}
    </div>;
  }

  return <div className={`group-operation-approval risk-${approval.risk.toLowerCase()}`}>
    <div className="group-operation-title"><div><ShieldCheck size={19} weight="duotone" /><span><b>{GROUP_OPERATION_LABELS[approval.action] || approval.action}</b><small>{approval.risk === "HIGH" ? "高风险操作" : "中风险操作"} · {formatGroupOperationExpiry(approval.expiresAt)}</small></span></div><i>{approval.risk}</i></div>
    <dl>{buildGroupOperationDetails(approval.operation).map(([label, value]) => <div key={label}><dt>{label}</dt><dd>{value}</dd></div>)}</dl>
    <label><span>请输入确认短语：<code>{approval.confirmationPhrase}</code></span><input value={confirmationText} onChange={(event) => setConfirmationText(event.target.value)} autoComplete="off" placeholder="逐字输入上方确认短语" /></label>
    <div className="group-operation-actions"><small>审批单五分钟内有效且只能执行一次，结果会写入审计日志</small><button type="button" disabled={executing || confirmationText.trim() !== approval.confirmationPhrase} onClick={() => void approve()}>{executing ? "执行中…" : "确认并执行"}</button></div>
    {error && <p className="group-operation-error" role="alert">{error}</p>}
  </div>;
}

/** 将固定群管理 DTO 转成只读摘要，不展示空值或任何认证信息。 */
function buildGroupOperationDetails(operation: Record<string, unknown>): Array<[string, string]> {
  const fields: Array<[string, unknown]> = [
    ["群号", operation.groupId],
    ["目标成员", operation.userId],
    ["禁言时长", operation.durationSeconds ? `${operation.durationSeconds} 秒` : ""],
    ["启用状态", typeof operation.enable === "boolean" ? (operation.enable ? "启用" : "关闭") : ""],
    ["群名片", operation.card],
    ["新群名", operation.groupName],
    ["公告内容", operation.content],
    ["消息 ID", operation.messageId],
  ];
  return fields.filter(([, value]) => value !== undefined && value !== null && value !== "").map(([label, value]) => [label, String(value)]);
}

/** 将审批有效期转换为用户可读文本；解析失败时仍明确提示该审批存在有效期。 */
function formatGroupOperationExpiry(expiresAt: string) {
  const date = new Date(expiresAt);
  return Number.isNaN(date.getTime()) ? "审批即将过期" : `有效至 ${date.toLocaleTimeString("zh-CN", { hour: "2-digit", minute: "2-digit" })}`;
}

/** 将进度卡片中的原始消息压缩为单行短语，完整内容仍只在上下文弹窗中展示。 */
function compactProgressText(value: string, maxLength = 42) {
  const compact = value.replace(/\s+/g, " ").trim();
  return compact.length > maxLength ? `${compact.slice(0, maxLength)}…` : compact;
}

/** 独立选择需要监控的会话，私聊与群聊分列展示，并支持先全选再排除少量对象。 */
function Monitoring({ contacts, profiles, error, busy, onRetry, onSave }: { contacts: QqContact[]; profiles: ConversationProfile[]; error: string; busy: boolean; onRetry: () => Promise<void>; onSave: (privateIds: string[], groupIds: string[]) => Promise<void> }) {
  const [privateIds, setPrivateIds] = useState<string[]>([]);
  const [groupIds, setGroupIds] = useState<string[]>([]);
  const [keyword, setKeyword] = useState("");

  useEffect(() => {
    setPrivateIds(profiles.find((item) => item.chatType === "private")?.chatIds || []);
    setGroupIds(profiles.find((item) => item.chatType === "group")?.chatIds || []);
  }, [profiles]);

  const privateContacts = contacts.filter((item) => item.type === "private");
  const groupContacts = contacts.filter((item) => item.type === "group");
  const normalizedKeyword = keyword.trim().toLowerCase();
  const matchesKeyword = (item: QqContact) => !normalizedKeyword
    || `${item.name} ${item.remark} ${item.id}`.toLowerCase().includes(normalizedKeyword);
  const visiblePrivateContacts = privateContacts.filter(matchesKeyword);
  const visibleGroupContacts = groupContacts.filter(matchesKeyword);

  /** 切换单个联系人；全选后再次点击某项即可将其排除。 */
  function toggle(contact: QqContact) {
    const ids = contact.type === "private" ? privateIds : groupIds;
    const update = contact.type === "private" ? setPrivateIds : setGroupIds;
    update(ids.includes(contact.id) ? ids.filter((id) => id !== contact.id) : [...ids, contact.id]);
  }

  /** 渲染单个监控对象，头像规则与消息空间保持一致。 */
  function renderContact(contact: QqContact) {
    const selectedIds = contact.type === "private" ? privateIds : groupIds;
    const selected = selectedIds.includes(contact.id);
    return <button type="button" className={selected ? "selected" : ""} key={`${contact.type}-${contact.id}`} onClick={() => toggle(contact)}>
      <ConversationListAvatar platform="qq" chatType={contact.type} chatId={contact.id} name={contact.name || contact.id} />
      <span><b>{contact.name || contact.id}</b><small>{contact.remark || contact.id}</small></span>
      <i>{selected ? "已监控" : "不监控"}</i>
    </button>;
  }

  return <div className="monitor-page">
    <section className="settings-header">
      <div><p className="eyebrow">MESSAGE MONITORING</p><h2>消息监控</h2><p>选择需要进入重要提醒和跨平台摘要的会话。</p></div>
      <button className="outline-button" disabled={busy} onClick={() => void onSave(privateIds, groupIds)}>{busy ? "保存中…" : "保存监控范围"}</button>
    </section>
    <section className="monitor-selector">
      {error && <div className="monitor-contact-error"><div><b>QQ 会话列表读取失败</b><span>{error}</span></div><button type="button" disabled={busy} onClick={() => void onRetry()}><ArrowClockwise size={15} />{busy ? "正在重试" : "重试读取"}</button></div>}
      <div className="monitor-select-toolbar">
        <div className="contact-search"><MagnifyingGlass size={18} /><input value={keyword} onChange={(event) => setKeyword(event.target.value)} placeholder="搜索好友、群聊或号码" /></div>
        <span>已选 {privateIds.length + groupIds.length} / {contacts.length}</span>
      </div>
      <div className="monitor-contact-columns">
        <section>
          <header><div><b>私聊</b><small>{privateIds.length} / {privateContacts.length} 已监控</small></div><button type="button" onClick={() => setPrivateIds(privateIds.length === privateContacts.length ? [] : privateContacts.map((item) => item.id))}>{privateIds.length === privateContacts.length ? "取消全部" : "一键全选"}</button></header>
          <div className="monitor-contact-list">{visiblePrivateContacts.length ? visiblePrivateContacts.map(renderContact) : <p>没有匹配的私聊</p>}</div>
        </section>
        <section>
          <header><div><b>群聊</b><small>{groupIds.length} / {groupContacts.length} 已监控</small></div><button type="button" onClick={() => setGroupIds(groupIds.length === groupContacts.length ? [] : groupContacts.map((item) => item.id))}>{groupIds.length === groupContacts.length ? "取消全部" : "一键全选"}</button></header>
          <div className="monitor-contact-list">{visibleGroupContacts.length ? visibleGroupContacts.map(renderContact) : <p>没有匹配的群聊</p>}</div>
        </section>
      </div>
    </section>
  </div>;
}

/** 将多行输入转换成去重列表，供成功条件、禁止事项等结构化字段复用。 */
function parseProfileLines(value: string) {
  return value.split(/\r?\n/).map((item) => item.trim()).filter((item, index, all) => Boolean(item) && all.indexOf(item) === index);
}

/**
 * 渲染 Conversation Profile 2.0 编辑器。
 * 权限、审批、知识库和记忆继续由原表单控制，这里只编辑业务上下文和资产引用。
 */
function ProfileContextEditor({ value, onChange, secureAssets, onOpenAssetManager }: { value: ConversationProfileContext; onChange: (value: ConversationProfileContext) => void; secureAssets: SecureAsset[]; onOpenAssetManager: () => void }) {
  /** 更新我的身份模块，同时保留其他结构化模块。 */
  const updateIdentity = (patch: Partial<ConversationProfileContext["identity"]>) => onChange({ ...value, identity: { ...value.identity, ...patch } });
  /** 更新对话背景模块，同时保留其他结构化模块。 */
  const updateBackground = (patch: Partial<ConversationProfileContext["background"]>) => onChange({ ...value, background: { ...value.background, ...patch } });
  /** 更新对话任务模块，同时保留其他结构化模块。 */
  const updateTask = (patch: Partial<ConversationProfileContext["task"]>) => onChange({ ...value, task: { ...value.task, ...patch } });
  /** 更新业务规则模块，同时保留其他结构化模块。 */
  const updateBusinessRules = (patch: Partial<ConversationProfileContext["businessRules"]>) => onChange({ ...value, businessRules: { ...value.businessRules, ...patch } });

  /** 根据已保存的身份字段还原预设；旧数据不匹配任何预设时自动进入自定义模式。 */
  const identityPreset = (() => {
    if ((!value.identity.representedPerson || value.identity.representedPerson === "本人") && (!value.identity.role || value.identity.role === "本人")) return "SELF";
    if (value.identity.role === "店铺客服") return "SHOP_SERVICE";
    if (value.identity.role === "项目负责人") return "PROJECT_OWNER";
    if (value.identity.role === "委托助理") return "ASSISTANT";
    return "CUSTOM";
  })();

  /** 应用常用代表身份，减少创建设定时必须手工输入的内容。 */
  const applyIdentityPreset = (preset: string) => {
    const presets: Record<string, Pick<ConversationProfileContext["identity"], "representedPerson" | "role">> = {
      SELF: { representedPerson: "本人", role: "本人" },
      SHOP_SERVICE: { representedPerson: "账号主人", role: "店铺客服" },
      PROJECT_OWNER: { representedPerson: "项目负责人", role: "项目负责人" },
      ASSISTANT: { representedPerson: "委托人", role: "委托助理" },
      CUSTOM: { representedPerson: "", role: "" },
    };
    updateIdentity(presets[preset] ?? presets.CUSTOM);
  };

  return <section className="full profile-context-editor">
    <div className="profile-context-heading"><div><b>会话目标</b><small>只填写你真正掌握的信息，其余内容交给 Agent 从历史对话中理解。</small></div><span>v{value.version || 2}</span></div>
    <div className="profile-primary-grid">
      <label>代表身份<select value={identityPreset} onChange={(event) => applyIdentityPreset(event.target.value)}><option value="SELF">本人（默认）</option><option value="SHOP_SERVICE">店铺 / 售后客服</option><option value="PROJECT_OWNER">项目负责人</option><option value="ASSISTANT">受托助理</option><option value="CUSTOM">自定义</option></select><small>决定 Agent 以谁的身份参与会话。</small></label>
      {identityPreset === "CUSTOM" && <div className="profile-custom-identity"><label>代表对象<input value={value.identity.representedPerson} onChange={(event) => updateIdentity({ representedPerson: event.target.value })} placeholder="例如：账号主人 freeze" /></label><label>角色说明<input value={value.identity.role} onChange={(event) => updateIdentity({ role: event.target.value })} placeholder="例如：社团负责人" /></label></div>}
      <label>背景补充（可选）<textarea value={value.background.origin} onChange={(event) => updateBackground({ origin: event.target.value })} placeholder="只补充历史聊天中没有的信息，例如：对方是刚认识的二手商品买家" /><small>历史事件与当前进展会由会话认知卡自动整理。</small></label>
      <label>本次会话任务（可选）<textarea value={value.task.objective} onChange={(event) => updateTask({ objective: event.target.value })} placeholder="例如：和对方确认明天下午见面时间，确认后结束代理并汇报" /><small>留空时 Agent 只按照设定处理当前消息，不主动推进额外目标。</small></label>
    </div>
    <div className="profile-cognition-hint"><div className="profile-cognition-mark">AI</div><div><b>会话认知卡将在启用历史后自动生成</b><p>对方身份、双方关系、交流习惯、已知事实和当前进展都从有依据的历史消息中提取；证据不足时保持未知，不要求你逐项填写。</p></div></div>
    <details className="profile-advanced"><summary><span>高级设置</span><small>表达边界、任务约束、业务规则与安全资产</small></summary><div className="profile-context-grid">
      <label className="full">补充表达要求<textarea value={value.identity.speakingStyle} onChange={(event) => updateIdentity({ speakingStyle: event.target.value })} placeholder="可选，例如：像熟人私聊一样简短自然，少用句末标点" /></label>
      <label className="full">禁用表达<textarea value={value.identity.forbiddenExpressions.join("\n")} onChange={(event) => updateIdentity({ forbiddenExpressions: parseProfileLines(event.target.value) })} placeholder={"每行一条，例如：\n我先确认一下\n作为一个 AI"} /></label>
      <label>截止时间<input value={value.task.deadline} onChange={(event) => updateTask({ deadline: event.target.value })} placeholder="自然语言或 ISO 时间" /></label>
      <span />
      <label>成功条件<textarea value={value.task.successCriteria.join("\n")} onChange={(event) => updateTask({ successCriteria: parseProfileLines(event.target.value) })} placeholder="每行一条" /></label>
      <label>禁止事项<textarea value={value.task.prohibitedActions.join("\n")} onChange={(event) => updateTask({ prohibitedActions: parseProfileLines(event.target.value) })} placeholder="每行一条" /></label>
      <div className="full profile-context-subheading"><b>业务规则</b><small>只有涉及报价、退款或交付时才需要配置。</small></div>
      <label>报价规则<textarea value={value.businessRules.pricingPolicy} onChange={(event) => updateBusinessRules({ pricingPolicy: event.target.value })} /></label>
      <label>最低价<input value={value.businessRules.minimumPrice} onChange={(event) => updateBusinessRules({ minimumPrice: event.target.value })} placeholder="例如：40 元，不含运费" /></label>
      <label>退款规则<textarea value={value.businessRules.refundPolicy} onChange={(event) => updateBusinessRules({ refundPolicy: event.target.value })} /></label>
      <label>交付条件<textarea value={value.businessRules.deliveryConditions} onChange={(event) => updateBusinessRules({ deliveryConditions: event.target.value })} /></label>
      <label className="full">硬性约束<textarea value={value.businessRules.hardConstraints.join("\n")} onChange={(event) => updateBusinessRules({ hardConstraints: parseProfileLines(event.target.value) })} placeholder="每行一条；规则不会自动授予工具权限" /></label>
      <div className="full profile-context-subheading"><b>可用资产</b><small>只保存加密资产引用，不在会话设定中保存正文。</small></div>
      <div className="full"><SecureAssetReferenceEditor assets={secureAssets} references={value.assets} onChange={(assets) => onChange({ ...value, assets })} onOpenManager={onOpenAssetManager} /></div>
    </div></details>
    <p className="profile-context-boundary">知识来源、工具权限、审批策略和记忆策略继续在下方独立配置。任务目标不会绕过这些安全边界。</p>
  </section>;
}

/** 渲染横向设定集规则清单，让会话范围、人格和回复策略一眼可见。 */
/** 渲染新建会话设定的编辑器，并支持联系人搜索与多种 Skill 来源。 */
function ProfileComposer({ open, editing, draft, onDraftChange, onSubmit, onInstallGithub, onClose, busy, contacts, contactKeyword, onContactKeywordChange, skills, models, secureAssets, onSaveSecureAsset, onDeleteSecureAsset }: { open: boolean; editing: boolean; draft: ConversationProfileDraft; onDraftChange: (draft: ConversationProfileDraft) => void; onSubmit: (event: FormEvent) => void; onInstallGithub: () => Promise<string>; onClose: () => void; busy: boolean; contacts: QqContact[]; contactKeyword: string; onContactKeywordChange: (value: string) => void; skills: SkillDescriptor[]; models: ModelProfile[]; secureAssets: SecureAsset[]; onSaveSecureAsset: (assetId: string, draft: SecureAssetDraft) => Promise<void>; onDeleteSecureAsset: (asset: SecureAsset) => Promise<void> }) {
  const [skillInstallFeedback, setSkillInstallFeedback] = useState<{ kind: "idle" | "loading" | "success" | "error"; text: string }>({ kind: "idle", text: "" });
  const [assetManagerOpen, setAssetManagerOpen] = useState(false);

  /** 弹窗打开时允许按 Esc 关闭，行为与客户端其他配置弹窗保持一致。 */
  useEffect(() => {
    if (!open) return undefined;
    const previousOverflow = document.body.style.overflow;
    document.body.style.overflow = "hidden";
    const handleKeyDown = (event: KeyboardEvent) => {
      if (event.key === "Escape") onClose();
    };
    window.addEventListener("keydown", handleKeyDown);
    return () => {
      document.body.style.overflow = previousOverflow;
      window.removeEventListener("keydown", handleKeyDown);
    };
  }, [open, onClose]);

  /** 用户切换安装来源或关闭弹窗后清理旧反馈，避免上一次结果误导本次操作。 */
  useEffect(() => {
    if (!open || draft.skillMode !== "github") {
      setSkillInstallFeedback({ kind: "idle", text: "" });
    }
  }, [open, draft.skillMode]);

  if (!open) return null;
  const visibleContacts = contacts.filter((item) => item.type === draft.chatType && `${item.name} ${item.remark} ${item.id}`.toLowerCase().includes(contactKeyword.trim().toLowerCase()));
  const selectedContacts = contacts.filter((item) => draft.contactIds.includes(item.id));
  const personalSkills = skills.filter((item) => item.sourceType === "personal");
  const installedSkills = skills.filter((item) => item.sourceType !== "personal");
  const displayedSkills = draft.skillMode === "personal" ? personalSkills : installedSkills;
  const selectedSkills = skills.filter((item) => draft.skillReferences.includes(item.reference));
  const unavailableSelectedReferences = draft.skillReferences.filter((reference) =>
    !skills.some((skill) => skill.reference === reference),
  );

  /** 执行 GitHub Skill 安装，并把进度或错误留在用户当前可见的弹窗内。 */
  async function handleGithubInstall() {
    setSkillInstallFeedback({ kind: "loading", text: "正在读取仓库中的 skill.json 或 SKILL.md…" });
    try {
      const message = await onInstallGithub();
      setSkillInstallFeedback({ kind: "success", text: message });
    } catch (error) {
      setSkillInstallFeedback({
        kind: "error",
        text: error instanceof Error ? error.message : "安装 GitHub Skill 失败",
      });
    }
  }

  /** 添加或移除一条设定所绑定的 QQ 好友或群聊。 */
  const toggleContact = (contactId: string) => onDraftChange({ ...draft, contactIds: draft.contactIds.includes(contactId) ? draft.contactIds.filter((item) => item !== contactId) : [...draft.contactIds, contactId] });

  /** 添加或移除 Skill，并同步旧版单 Skill 字段，保证新旧后端数据可以平滑共存。 */
  const toggleSkill = (skill: SkillDescriptor) => {
    const selected = draft.skillReferences.includes(skill.reference);
    const nextReferences = selected
      ? draft.skillReferences.filter((item) => item !== skill.reference)
      : [...draft.skillReferences, skill.reference];
    onDraftChange({
      ...draft,
      skillReferences: nextReferences,
      skillReference: nextReferences[0] || "",
    });
  };

  /** 移除目录中已不存在的旧 Skill 引用，让用户可以自行修复迁移后的设定。 */
  const removeUnavailableSkill = (reference: string) => {
    const nextReferences = draft.skillReferences.filter((item) => item !== reference);
    onDraftChange({
      ...draft,
      skillReferences: nextReferences,
      skillReference: nextReferences[0] || "",
    });
  };
  return (
    <div className="profile-editor-backdrop" onMouseDown={onClose}>
    <section className="profile-editor profile-rule-editor" role="dialog" aria-modal="true" aria-label={editing ? "编辑会话设定" : "新建会话设定"} onMouseDown={(event) => event.stopPropagation()}>
      <div className="panel-title editor-heading">
        <div><p className="eyebrow">{editing ? "EDIT CONVERSATION RULE" : "NEW CONVERSATION RULE"}</p><h2>{editing ? "编辑会话设定" : "新建设定"}</h2><span>为指定会话设置人格、Skill、模型和回复边界。</span></div>
        <button className="text-button editor-close" onClick={onClose} type="button">关闭</button>
      </div>
      <form className="profile-form" onSubmit={onSubmit}>
        <label>设定名称<input value={draft.name} onChange={(event) => onDraftChange({ ...draft, name: event.target.value })} placeholder="例如：重要私聊的克制回复" required /></label>
        <label>会话类型<select value={draft.chatType} onChange={(event) => onDraftChange({ ...draft, chatType: event.target.value, contactIds: [] })}><option value="private">QQ 私聊</option><option value="group">QQ 群聊</option></select></label>
        <label className="full">搜索并添加生效会话
          <div className="contact-search"><MagnifyingGlass size={18} /><input value={contactKeyword} onChange={(event) => onContactKeywordChange(event.target.value)} placeholder="搜索好友昵称、群名、备注或 QQ 号" /></div>
          <div className="contact-picker">
            {visibleContacts.length === 0 ? <span className="empty">没有匹配的 {draft.chatType === "private" ? "好友" : "群聊"}。请确认 NapCat 已登录且好友/群列表可读取。</span> : visibleContacts.map((item) => <button type="button" className={draft.contactIds.includes(item.id) ? "contact-option selected" : "contact-option"} key={item.id} onClick={() => toggleContact(item.id)}><span className="contact-avatar">{(item.name || item.id).slice(0, 1)}</span><span><b>{item.name || item.id}</b><small>{item.remark || `${item.type === "private" ? "好友" : "群聊"} · ${item.id}`}</small></span></button>)}
          </div>
          {selectedContacts.length > 0 && <div className="selected-contacts"><small>已选择</small>{selectedContacts.map((item) => <span key={item.id}>{item.name || item.id}<button aria-label={`移除 ${item.name || item.id}`} type="button" onClick={() => toggleContact(item.id)}>×</button></span>)}</div>}
        </label>
        <ProfileContextEditor value={draft.profileContext} onChange={(profileContext) => onDraftChange({ ...draft, profileContext })} secureAssets={secureAssets} onOpenAssetManager={() => setAssetManagerOpen(true)} />
        <label className="full">Skill 装载
          <div className="skill-modes">
            <button type="button" className={draft.skillMode === "prompt" ? "selected" : ""} onClick={() => onDraftChange({ ...draft, skillMode: "prompt", skillReference: "", skillReferences: [], githubReference: "" })}><FileText size={18} /><span><b>仅人格提示</b><small>清空已选 Skill，只使用会话提示</small></span></button>
            <button type="button" className={draft.skillMode === "personal" ? "selected" : ""} onClick={() => onDraftChange({ ...draft, skillMode: "personal", githubReference: "" })}><Sparkle size={18} /><span><b>我的 Skill</b><small>选择从本人授权样本中提炼的风格</small></span></button>
            <button type="button" className={draft.skillMode === "local" ? "selected" : ""} onClick={() => onDraftChange({ ...draft, skillMode: "local", githubReference: "" })}><HardDrives size={18} /><span><b>本地 Skill</b><small>组合内置或已经安装的能力</small></span></button>
            <button type="button" className={draft.skillMode === "github" ? "selected" : ""} onClick={() => onDraftChange({ ...draft, skillMode: "github" })}><GithubLogo size={18} /><span><b>从 GitHub 安装</b><small>先安装校验，再加入当前设定</small></span></button>
          </div>
        </label>
        {(draft.skillMode === "personal" || draft.skillMode === "local") && <section className="full skill-catalog"><div className="skill-catalog-heading"><b>{draft.skillMode === "personal" ? "选择个人风格" : "选择已安装能力"}</b><small>支持组合多个 Skill；保存前会按 social_reply 路由再次校验。</small></div>{displayedSkills.length === 0 ? <p className="empty">{draft.skillMode === "personal" ? "当前还没有达到发布条件的个人 Skill。" : "当前没有可用的本地 Skill。"}</p> : <div className="skill-catalog-grid">{displayedSkills.map((skill) => { const incompatible = skill.applicableRoutes.length > 0 && !skill.applicableRoutes.includes("social_reply"); const selected = draft.skillReferences.includes(skill.reference); return <button type="button" key={skill.reference} className={selected ? "skill-card selected" : "skill-card"} disabled={incompatible} onClick={() => toggleSkill(skill)}><span className="skill-card-title"><b>{skill.name}</b>{selected && <CheckCircle size={16} weight="fill" />}</span><small>{skill.description || "未填写 Skill 说明"}</small><span className="skill-card-meta"><i>{skill.sourceType}</i><i>v{skill.version || "1.0.0"}</i>{incompatible && <i className="incompatible">不支持社交回复</i>}</span>{skill.toolPolicy.allow.length > 0 && <span className="skill-tool-list">工具：{skill.toolPolicy.allow.join("、")}</span>}</button>; })}</div>}</section>}
        {draft.skillMode === "github" && <section className="full github-skill-installer"><div><b>安装 GitHub Skill</b><small>支持普通 GitHub 仓库 URL 和 <code>github://owner/repo@ref/path</code>。服务优先读取 <code>skill.json</code>，不存在时安全转换 <code>SKILL.md</code>；不会执行仓库代码。</small></div><div className="github-skill-action"><input value={draft.githubReference} onChange={(event) => { onDraftChange({ ...draft, githubReference: event.target.value }); setSkillInstallFeedback({ kind: "idle", text: "" }); }} placeholder="https://github.com/owner/repo 或 github://owner/repo/path" /><button type="button" disabled={busy || !draft.githubReference.trim()} onClick={() => void handleGithubInstall()}>{busy ? "正在安装…" : "安装并校验"}</button></div>{skillInstallFeedback.text && <p className={`skill-install-feedback ${skillInstallFeedback.kind}`}>{skillInstallFeedback.text}</p>}<small className="skill-import-boundary">标准 SKILL.md 的正文会作为只读提示载入；references/、examples/ 和脚本不会自动执行或注入。</small></section>}
        {(selectedSkills.length > 0 || unavailableSelectedReferences.length > 0) && <section className="full selected-skill-panel"><div><b>当前已装载 {draft.skillReferences.length} 个 Skill</b><small>多个 Skill 的提示约束会合并，工具权限取交集，避免组合后扩大权限。</small></div><div>{selectedSkills.map((skill) => <span key={skill.reference}>{skill.name}<button type="button" aria-label={`移除 ${skill.name}`} onClick={() => toggleSkill(skill)}>×</button></span>)}{unavailableSelectedReferences.map((reference) => <span className="missing-skill" key={reference}>失效 · {reference}<button type="button" aria-label={`移除失效引用 ${reference}`} onClick={() => removeUnavailableSkill(reference)}>×</button></span>)}</div></section>}
        <label className="full">补充人格提示<textarea value={draft.systemPrompt} onChange={(event) => onDraftChange({ ...draft, systemPrompt: event.target.value })} placeholder="可选。用于补充当前会话的人格、语气和边界，不会覆盖 Skill 的安全约束。" /><small>即使已经装载 Skill，这段提示仍会作为当前会话的补充要求传给 Agent。</small></label>
        <label className="full knowledge-base-field">外部知识库
          <textarea value={draft.knowledgeBaseSources} onChange={(event) => onDraftChange({ ...draft, knowledgeBaseSources: event.target.value })} placeholder={"每行一个来源，例如：\nhttps://example.com/faq\nC:\\Users\\freeze\\Documents\\product-notes.md"} />
          <small>支持公开 HTTP(S) 页面，以及本机 .txt、.md、.json、.csv、.log 文件。运行时只检索少量相关片段；资料内容不会改变 Agent 的系统规则。</small>
        </label>
        <label className="full">执行模型<select value={draft.modelProfileId} onChange={(event) => onDraftChange({ ...draft, modelProfileId: event.target.value })}><option value="">使用当前默认模型</option>{models.filter((model) => model.enabled).map((model) => <option key={model.id} value={model.id}>{model.name} · {model.model}</option>)}</select><small>{models.length === 0 ? "尚未配置模型时只能使用基础规则回复，复杂人格提示不会被完整执行。" : "建议绑定一个已启用且已配置 API Key 的模型。"}</small></label>
          <label>回复策略<select value={draft.replyMode} onChange={(event) => onDraftChange({ ...draft, replyMode: event.target.value })}><option value="DRAFT_ONLY">只生成草稿，等待确认</option><option value="AUTO_REPLY">自动回复</option></select></label>
          <label>候选回复审批<select value={draft.reviewMode} onChange={(event) => onDraftChange({ ...draft, reviewMode: event.target.value })}><option value="STRICT_HANDOFF">严格审批，越界时人工接管</option><option value="AUTO_REWRITE">无需人工审批，自动纠偏后发送</option></select><small>{draft.reviewMode === "AUTO_REWRITE" ? "ReviewAgent 会删除无依据内容并重新审批；确定性高风险底线仍不会放行。" : "任何超出历史、提示词或 Skill 的内容都会停止发送并请求你接管。"}</small></label>
        <label>单条最多字符<input type="number" min="8" max="80" value={draft.maxReplyChars} onChange={(event) => onDraftChange({ ...draft, maxReplyChars: Number(event.target.value) })} /><small>默认 24。超过此长度才会考虑拆分或截断。</small></label>
        <label>长消息分段概率<input type="number" min="0" max="100" value={draft.splitReplyChancePercent} disabled={!draft.splitLongReply} onChange={(event) => onDraftChange({ ...draft, splitReplyChancePercent: Number(event.target.value) })} /><small>0 从不拆分，100 总是拆分。</small></label>
         <div className="toggle-group"><label><input type="checkbox" checked={draft.splitLongReply} onChange={(event) => onDraftChange({ ...draft, splitLongReply: event.target.checked })} /><span>允许长回复拆成多条气泡</span></label></div>
        {draft.chatType === "group" && <section className="profile-history-options group-operation-permission"><b>QQ 群管理权限</b><p>默认只能查询当前群信息。开启后，只有当前登录 QQ 本人发出的明确控制消息可以提出禁言、群公告、群名、管理员和移出成员等操作，普通群成员无法触发。</p><div className="toggle-group"><label><input type="checkbox" checked={draft.groupManagementEnabled} onChange={(event) => onDraftChange({ ...draft, groupManagementEnabled: event.target.checked })} /><span>允许 Agent 提出当前群的管理操作</span></label></div><small>所有写操作都不会直接执行，而是生成 5 分钟有效的一次性审批单；高风险操作还必须输入完整确认短语。</small></section>}
         {draft.chatType === "private" && <section className="profile-history-options"><b>私聊上下文</b><p>当前连续聊天始终保留必要的短期上下文；开启后还可读取上一段已经结束的近期聊天。</p><div className="toggle-group"><label><input type="checkbox" checked={draft.privateHistoryEnabled} onChange={(event) => onDraftChange({ ...draft, privateHistoryEnabled: event.target.checked })} /><span>允许读取跨时段的私聊历史</span></label></div><div className="profile-history-limits"><label>最多消息数<input type="number" min="1" max="50" disabled={!draft.privateHistoryEnabled} value={draft.historyMaxMessages} onChange={(event) => onDraftChange({ ...draft, historyMaxMessages: Number(event.target.value) })} /></label><label>最多文本字符<input type="number" min="200" max="12000" disabled={!draft.privateHistoryEnabled} value={draft.historyMaxChars} onChange={(event) => onDraftChange({ ...draft, historyMaxChars: Number(event.target.value) })} /></label></div><small>关闭不会让当前连续聊天失忆；该开关也不会自动开启个人风格训练。</small></section>}
         {draft.chatType === "private" && <section className="profile-history-options training-consent"><b>历史消息训练授权</b><p>开启后，系统会获取该私聊中本账号发送的历史消息，并统一视为可用于提炼你的个人表达 Skill。</p><div className="toggle-group"><label><input type="checkbox" checked={draft.historyTrainingEnabled} onChange={(event) => onDraftChange({ ...draft, historyTrainingEnabled: event.target.checked })} /><span>允许该会话的历史消息用于个人风格训练</span></label></div><small>注意：NapCat 无法可靠追溯旧消息是否曾由其他自动化工具代发。开启即表示你接受将这些本账号历史消息作为自己的表达样本。</small></section>}
        <section className="profile-history-options memory-consent"><b>长期记忆候选授权</b><p>开启后，只从连接器明确识别为“本账号真人发送”的新消息中提取稳定事实候选。</p><div className="toggle-group"><label><input type="checkbox" checked={draft.profileContext.memoryPolicy?.extractionEnabled === true} onChange={(event) => onDraftChange({ ...draft, profileContext: { ...draft.profileContext, memoryPolicy: { extractionEnabled: event.target.checked } } })} /><span>允许该会话产生长期记忆候选</span></label></div><small>不会读取对方发言作为你的事实，不会使用 Agent 代发内容，也不会自动确认；候选需要你在“长期记忆”中审核。</small></section>
          <section className="full monitor-settings">
            <div className="monitor-settings-title"><b>消息监控与摘要</b><small>决定何时立即提醒，以及普通消息何时聚合总结。</small></div>
            <div className="monitor-settings-grid">
              <label>监控模式<select value={draft.notificationMode} onChange={(event) => onDraftChange({ ...draft, notificationMode: event.target.value })}><option value="AUTO">自动判断快慢通道</option><option value="URGENT_ONLY">仅重要消息提醒</option><option value="DIGEST_ONLY">只进入定时摘要</option><option value="MUTE">不监控</option></select></label>
              <label>重要关键词<input value={draft.notificationKeywords} onChange={(event) => onDraftChange({ ...draft, notificationKeywords: event.target.value })} placeholder="通知，截止，紧急，@我" /></label>
              <label>最长等待时间（分钟）<input type="number" min="1" max="1440" value={Math.round(draft.digestWindowSeconds / 60)} onChange={(event) => onDraftChange({ ...draft, digestWindowSeconds: Math.max(60, Number(event.target.value) * 60) })} /></label>
              <label>消息数量阈值<input type="number" min="2" max="500" value={draft.digestMaxMessages} onChange={(event) => onDraftChange({ ...draft, digestMaxMessages: Number(event.target.value) })} /></label>
            </div>
            <div className="toggle-group"><label><input type="checkbox" checked={draft.includeUrgentInDigest} onChange={(event) => onDraftChange({ ...draft, includeUrgentInDigest: event.target.checked })} /><span>快通道消息仍保留在后续摘要中</span></label></div>
          </section>
        <div className="form-action"><small>自动回复属于高风险操作，建议先使用草稿模式验证设定。</small><button disabled={busy} type="submit">{busy ? "正在保存…" : editing ? "保存修改" : "保存设定"}</button></div>
      </form>
    </section>
    <SecureAssetManagerDialog open={assetManagerOpen} assets={secureAssets} busy={busy} onClose={() => setAssetManagerOpen(false)} onSave={onSaveSecureAsset} onDelete={onDeleteSecureAsset} />
    </div>
  );
}

/** 渲染带编辑和删除操作的会话设定列表。 */
function Profiles({ profiles, models, onCreate, onEdit, onDelete }: { profiles: ConversationProfile[]; models: ModelProfile[]; onCreate: () => void; onEdit: (profile: ConversationProfile) => void; onDelete: (profile: ConversationProfile) => void }) {
  return <div className="settings-page"><section className="settings-header"><div><p className="eyebrow">CONVERSATION RULES</p><h2>设定集</h2><p>为不同会话定义人格、Skill、模型和自动回复边界。</p></div><button className="outline-button" onClick={onCreate}><Plus size={17} weight="bold" />新建设定</button></section><section className="settings-board profile-board"><div className="settings-labels"><span>设定名称</span><span>生效会话</span><span>Skill / 人格提示</span><span>回复策略</span><span>操作</span></div>{profiles.length === 0 ? <p className="empty settings-empty">尚未创建会话设定。你可以先为一个私聊或群聊定义回复规则。</p> : profiles.map((item) => <article className="setting-row" key={item.id}><div className="setting-name"><b>{item.name}</b><small>{item.enabled ? "已启用" : "已停用"} · 优先级 {item.priority}</small></div><span className="rule-chip">{describeScope(item)}</span><span className="rule-chip">{describePersona(item)}</span><span className="rule-chip wide">{describeReplyStrategy(item)}</span><div className="setting-actions"><button title="编辑设定" onClick={() => onEdit(item)}><PencilSimple size={16} /></button><button className="danger-action" title="删除设定" onClick={() => onDelete(item)}><Trash size={16} /></button></div></article>)}</section><section className="model-strip"><div><h3>模型配置</h3><p>人格提示需要可用模型才能完整执行；没有模型时只会使用基础规则回复。</p></div><div className="model-pills">{models.length === 0 ? <span className="rule-chip">尚未配置模型</span> : models.map((item) => <span className="rule-chip" key={item.id}>{item.isDefault ? "默认 · " : ""}{item.name} / {item.model}</span>)}</div></section></div>;
}

/** 保留旧版只读设定列表，便于后续迁移时对照字段。 */
function LegacyProfiles({ profiles, models, onCreate }: { profiles: ConversationProfile[]; models: ModelProfile[]; onCreate: () => void }) {
  return <div className="settings-page"><section className="settings-header"><div><p className="eyebrow">CONVERSATION RULES</p><h2>设定集</h2><p>为不同会话定义人格、Skill 和自动回复边界。</p></div><button className="outline-button" onClick={onCreate}><Plus size={17} weight="bold" />新建设定</button></section><section className="settings-board"><div className="settings-labels"><span>设定名称</span><span>生效会话</span><span>Skill / 人格提示</span><span>回复策略</span></div>{profiles.length === 0 ? <p className="empty settings-empty">尚未创建会话设定。你可以先为一个私聊或群聊定义回复规则。</p> : profiles.map((item) => <article className="setting-row" key={item.id}><div className="setting-name"><b>{item.name}</b><small>{item.enabled ? "已启用" : "已停用"} · 优先级 {item.priority}</small></div><span className="rule-chip">{describeScope(item)}</span><span className="rule-chip">{describePersona(item)}</span><span className="rule-chip wide">{describeReplyStrategy(item)}</span></article>)}</section><section className="model-strip"><div><h3>模型配置</h3><p>当前模型仅用于本地 Agent 调用，密钥始终不会显示在客户端。</p></div><div className="model-pills">{models.length === 0 ? <span className="rule-chip">尚未配置模型</span> : models.map((item) => <span className="rule-chip" key={item.id}>{item.isDefault ? "默认 · " : ""}{item.name} / {item.model}</span>)}</div></section></div>;
}

/** 展示候选长期记忆的审核队列；未确认事实永远不会直接提供给 Agent。 */
function Memories({ items, draft, editorOpen, editing, busy, onCreate, onEdit, onClose, onDraftChange, onSubmit, onVerify, onReject, onDelete, onViewEvidence, onResolveConflict }: {
  items: MemoryCandidate[];
  draft: MemoryCandidateDraft;
  editorOpen: boolean;
  editing: boolean;
  busy: boolean;
  onCreate: () => void;
  onEdit: (item: MemoryCandidate) => void;
  onClose: () => void;
  onDraftChange: (draft: MemoryCandidateDraft) => void;
  onSubmit: (event: FormEvent) => void;
  onVerify: (item: MemoryCandidate) => void;
  onReject: (item: MemoryCandidate) => void;
  onDelete: (item: MemoryCandidate) => void;
  onViewEvidence: (item: MemoryCandidate) => void;
  onResolveConflict: (item: MemoryCandidate, decision: "KEEP_VERIFIED" | "USE_CANDIDATE") => Promise<void>;
}) {
  const [conflictCandidate, setConflictCandidate] = useState<MemoryCandidate | null>(null);
  const groups = [
    { status: "CANDIDATE", title: "等待确认", note: "尚未进入 Agent", items: items.filter((item) => item.status === "CANDIDATE") },
    { status: "VERIFIED", title: "已确认", note: "按作用域参与回复", items: items.filter((item) => item.status === "VERIFIED") },
    { status: "ARCHIVED", title: "已归档", note: "拒绝、过期或被替代", items: items.filter((item) => item.status === "REJECTED" || item.status === "EXPIRED" || item.status === "SUPERSEDED") },
  ];
  const verifiedFacts = items.filter((item) => item.status === "VERIFIED");

  /** 判断候选是否与同一作用域中已确认的同属性事实值冲突。 */
  function conflictingVerifiedFacts(candidate: MemoryCandidate) {
    if (candidate.status !== "CANDIDATE") return [];
    return verifiedFacts.filter((verified) =>
      verified.subject === candidate.subject
      && verified.predicate === candidate.predicate
      && verified.scopeType === candidate.scopeType
      && verified.platform === candidate.platform
      && verified.scene === candidate.scene
      && verified.chatType === candidate.chatType
      && verified.chatId === candidate.chatId
      && verified.value !== candidate.value,
    );
  }

  /** 把数据库作用域转换为管理页面中的短标签。 */
  function describeMemoryScope(item: MemoryCandidate) {
    if (item.scopeType === "CONVERSATION") return `${item.platform} · ${item.chatType} · ${item.chatId}`;
    if (item.scopeType === "SCENE") return `场景 · ${item.scene}`;
    if (item.scopeType === "PLATFORM") return `平台 · ${item.platform}`;
    return "全部会话";
  }

  return (
    <div className="memory-page settings-page">
      <section className="settings-header">
        <div>
          <p className="eyebrow">VERIFIED MEMORY</p>
          <h2>长期记忆</h2>
          <p>候选事实必须由你确认。Agent 的历史输出和联系人陈述不会自动成为你的长期事实。</p>
        </div>
        <button className="outline-button" type="button" onClick={onCreate}>
          <Plus size={17} weight="bold" />添加候选
        </button>
      </section>

      <div className="memory-boundary">
        <ShieldCheck size={18} />
        <div>
          <b>可信边界</b>
          <span>只有“已确认”且未过期的记忆会按当前平台、场景或会话注入生成与审查链路。</span>
        </div>
      </div>

      <div className="memory-columns">
        {groups.map((group) => (
          <section className={`memory-column memory-${group.status.toLowerCase()}`} key={group.status}>
            <header>
              <div><b>{group.title}</b><small>{group.note}</small></div>
              <span>{group.items.length}</span>
            </header>
            <div>
              {group.items.length === 0 ? (
                <p className="memory-empty">暂无记录</p>
              ) : group.items.map((item) => (
                <article className="memory-card" key={item.id}>
                  <div className="memory-fact">
                    <small>{item.subject}</small>
                    <b>{item.predicate}</b>
                    <p>{item.value}</p>
                  </div>
                  <div className="memory-meta">
                    <span>{describeMemoryScope(item)}</span>
                    <span>{item.sourceEventIds.length > 0 ? `${item.sourceEventIds.length} 条来源事件` : "手工录入"}</span>
                    {item.expiresAt && <span>有效至 {formatTime(item.expiresAt)}</span>}
                  </div>
                  {Boolean(conflictingVerifiedFacts(item).length) && <p className="memory-conflict">发现同一作用域的已确认值，需要明确选择保留哪一项</p>}
                  {item.rejectionReason && <p className="memory-rejection">{item.rejectionReason}</p>}
                  <footer>
                    {item.status === "CANDIDATE" && (
                      <>
                        <button type="button" onClick={() => onEdit(item)} disabled={busy}>
                          <PencilSimple size={15} />编辑
                        </button>
                        {conflictingVerifiedFacts(item).length > 0 ? (
                          <button className="memory-conflict-action" type="button" onClick={() => setConflictCandidate(item)} disabled={busy}>
                            处理冲突
                          </button>
                        ) : (
                          <button className="memory-verify" type="button" onClick={() => onVerify(item)} disabled={busy}>
                            <CheckCircle size={15} />确认
                          </button>
                        )}
                        <button type="button" onClick={() => onReject(item)} disabled={busy}>拒绝</button>
                      </>
                    )}
                    {item.sourceEventIds.length > 0 && (
                      <button type="button" onClick={() => onViewEvidence(item)} disabled={busy}>
                        <ChatCircleDots size={15} />查看来源
                      </button>
                    )}
                    <button className="danger-action" type="button" onClick={() => onDelete(item)} disabled={busy}>
                      <Trash size={15} />删除
                    </button>
                  </footer>
                </article>
              ))}
            </div>
          </section>
        ))}
      </div>

      {conflictCandidate && (
        <div className="memory-editor-backdrop" role="presentation" onMouseDown={(event) => {
          if (event.currentTarget === event.target && !busy) setConflictCandidate(null);
        }}>
          <section className="memory-conflict-dialog" role="dialog" aria-modal="true" aria-label="处理长期记忆冲突">
            <header>
              <div>
                <p className="eyebrow">MEMORY CONFLICT</p>
                <h2>选择要保留的事实</h2>
                <span>服务端会原子更新状态，不会让新旧事实同时进入 Agent。</span>
              </div>
              <button className="text-button" type="button" onClick={() => setConflictCandidate(null)} disabled={busy}>关闭</button>
            </header>
            <div className="memory-conflict-comparison">
              <section>
                <small>当前已确认</small>
                {conflictingVerifiedFacts(conflictCandidate).map((item) => (
                  <article key={item.id}><b>{item.value}</b><span>{describeMemoryScope(item)}</span></article>
                ))}
              </section>
              <section className="candidate">
                <small>新候选</small>
                <article><b>{conflictCandidate.value}</b><span>{describeMemoryScope(conflictCandidate)}</span></article>
              </section>
            </div>
            <footer>
              <button type="button" disabled={busy} onClick={() => void onResolveConflict(conflictCandidate, "KEEP_VERIFIED").then(() => setConflictCandidate(null)).catch(() => undefined)}>
                保留已确认值
              </button>
              <button className="memory-verify" type="button" disabled={busy} onClick={() => void onResolveConflict(conflictCandidate, "USE_CANDIDATE").then(() => setConflictCandidate(null)).catch(() => undefined)}>
                {busy ? "正在处理…" : "采用候选值"}
              </button>
            </footer>
          </section>
        </div>
      )}

      {editorOpen && (
        <div
          className="memory-editor-backdrop"
          role="presentation"
          onMouseDown={(event) => { if (event.currentTarget === event.target) onClose(); }}
        >
          <section className="memory-editor" role="dialog" aria-modal="true" aria-label={editing ? "编辑长期记忆候选" : "添加长期记忆候选"}>
            <header>
              <div>
                <p className="eyebrow">{editing ? "EDIT MEMORY CANDIDATE" : "NEW MEMORY CANDIDATE"}</p>
                <h2>{editing ? "编辑候选记忆" : "添加候选记忆"}</h2>
                <span>保存后仍需再次确认，避免误操作直接污染 Agent 上下文。</span>
              </div>
              <button className="text-button" type="button" onClick={onClose}>关闭</button>
            </header>
            <form onSubmit={onSubmit}>
              <label>
                主体
                <input value={draft.subject} onChange={(event) => onDraftChange({ ...draft, subject: event.target.value })} placeholder="例如：对方、项目 A、我" required />
              </label>
              <label>
                属性
                <input value={draft.predicate} onChange={(event) => onDraftChange({ ...draft, predicate: event.target.value })} placeholder="例如：常用称呼、截止时间" required />
              </label>
              <label className="full">
                事实内容
                <textarea value={draft.value} onChange={(event) => onDraftChange({ ...draft, value: event.target.value })} placeholder="只写明确事实，不要粘贴整段聊天" required />
              </label>
              <label>
                作用域
                <select value={draft.scopeType} onChange={(event) => onDraftChange({ ...draft, scopeType: event.target.value as MemoryCandidateDraft["scopeType"] })}>
                  <option value="GLOBAL">全部会话</option>
                  <option value="PLATFORM">指定平台</option>
                  <option value="SCENE">指定场景</option>
                  <option value="CONVERSATION">指定会话</option>
                </select>
              </label>
              <label>
                过期时间（可选）
                <input type="datetime-local" value={draft.expiresAt} onChange={(event) => onDraftChange({ ...draft, expiresAt: event.target.value })} />
              </label>

              {draft.scopeType === "PLATFORM" && (
                <label className="full">
                  平台
                  <input value={draft.platform} onChange={(event) => onDraftChange({ ...draft, platform: event.target.value })} placeholder="qq" required />
                </label>
              )}
              {draft.scopeType === "SCENE" && (
                <>
                  <label>
                    平台（可选）
                    <input value={draft.platform} onChange={(event) => onDraftChange({ ...draft, platform: event.target.value })} placeholder="qq" />
                  </label>
                  <label>
                    场景
                    <input value={draft.scene} onChange={(event) => onDraftChange({ ...draft, scene: event.target.value })} placeholder="life / work" required />
                  </label>
                </>
              )}
              {draft.scopeType === "CONVERSATION" && (
                <>
                  <label>
                    平台
                    <input value={draft.platform} onChange={(event) => onDraftChange({ ...draft, platform: event.target.value })} placeholder="qq" required />
                  </label>
                  <label>
                    会话类型
                    <select value={draft.chatType} onChange={(event) => onDraftChange({ ...draft, chatType: event.target.value })} required>
                      <option value="">请选择</option>
                      <option value="private">私聊</option>
                      <option value="group">群聊</option>
                    </select>
                  </label>
                  <label className="full">
                    会话 ID
                    <input value={draft.chatId} onChange={(event) => onDraftChange({ ...draft, chatId: event.target.value })} required />
                  </label>
                </>
              )}
              <div className="form-action full">
                <small>候选状态不会进入 Agent，创建后请在列表中核对并确认。</small>
                <button type="submit" disabled={busy}>{busy ? "正在保存…" : editing ? "保存修改" : "保存候选"}</button>
              </div>
            </form>
          </section>
        </div>
      )}
    </div>
  );
}

/** 渲染厂商模型目录；预设会填入模型地址、模型 ID 和建议使用的 Agent 路由。 */
function Models({ profiles, draft, editorOpen, editingId, busy, onCreate, onPreset, onEdit, onDelete, onDraftChange, onSubmit, onClose }: { profiles: ModelProfile[]; draft: ModelProfileDraft; editorOpen: boolean; editingId: string; busy: boolean; onCreate: () => void; onPreset: (preset: ModelPreset) => void; onEdit: (profile: ModelProfile) => void; onDelete: (profile: ModelProfile) => void; onDraftChange: (draft: ModelProfileDraft) => void; onSubmit: (event: FormEvent) => void; onClose: () => void }) {
  return <div className="model-center"><section className="settings-header"><div><p className="eyebrow">MODEL PROFILES</p><h2>模型配置</h2><p>按任务分配模型：文字回复、视觉分析与 Agent 工作流可以分别使用不同模型。</p></div><button className="outline-button" onClick={onCreate}><Plus size={17} weight="bold" />配置自定义模型</button></section><section className="model-preset-panel"><div className="preset-heading"><div><b>推荐模型</b><span>预设会自动填写服务地址、模型 ID 和建议路由。视觉模型请保留 <code>vision_analysis</code>。</span></div><small>{profiles.find((item) => item.isDefault)?.name || "尚未设置默认模型"}</small></div><div className="preset-list">{CURRENT_MODEL_PRESETS.map((preset) => { const configured = profiles.some((item) => item.model === preset.model); return <button type="button" key={preset.model} onClick={() => onPreset(preset)}><VendorGlyph vendor={preset.vendor} /><span className="preset-name"><b>{preset.name}</b><small>{preset.note}</small></span><span className="capability-list">{(preset.capabilities || ["TEXT"]).map((capability) => <span className={`capability capability-${capability.toLowerCase()}`} key={capability}>{capability === "VISION" ? "视觉" : capability === "AGENT" ? "Agent" : "文本"}</span>)}</span><span className="ability-pill"><Brain size={11} />{preset.level}</span>{configured ? <CheckCircle className="preset-check" size={17} weight="fill" /> : <span className="preset-add"><Plus size={15} /></span>}</button>; })}</div><button className="custom-model-row" type="button" onClick={onCreate}><Plus size={15} />配置自定义模型</button></section><LegacyModels profiles={profiles} draft={draft} editorOpen={editorOpen} editingId={editingId} busy={busy} onCreate={onCreate} onEdit={onEdit} onDelete={onDelete} onDraftChange={onDraftChange} onSubmit={onSubmit} onClose={onClose} /></div>;
}

/** 使用本地矢量化字标呈现供应商图标，避免额外引入网络图片或第三方图标包。 */
function VendorGlyph({ vendor }: { vendor: ModelPreset["vendor"] }) {
  const labels: Record<ModelPreset["vendor"], string> = { DeepSeek: "DS", Qwen: "Q", GLM: "Z", Kimi: "K", MiniMax: "M" };
  return <span className={`vendor-glyph vendor-${vendor.toLowerCase()}`} aria-label={vendor}><i>{labels[vendor]}</i></span>;
}

/** 渲染模型高级参数编辑器和已保存配置列表。 */
function LegacyModels({ profiles, draft, editorOpen, editingId, busy, onCreate, onEdit, onDelete, onDraftChange, onSubmit, onClose }: { profiles: ModelProfile[]; draft: ModelProfileDraft; editorOpen: boolean; editingId: string; busy: boolean; onCreate: () => void; onEdit: (profile: ModelProfile) => void; onDelete: (profile: ModelProfile) => void; onDraftChange: (draft: ModelProfileDraft) => void; onSubmit: (event: FormEvent) => void; onClose: () => void }) {
  /** 添加或移除模型适用的 Agent route；空列表代表它可以作为全局模型。 */
  function toggleRoute(route: string) {
    onDraftChange({
      ...draft,
      supportedRoutes: draft.supportedRoutes.includes(route)
        ? draft.supportedRoutes.filter((item) => item !== route)
        : [...draft.supportedRoutes, route],
    });
  }

  return <div className="models-page"><section className="settings-header"><div><p className="eyebrow">MODEL PROFILES</p><h2>模型配置</h2><p>使用你自己的模型服务，并为不同 Agent route 选择不同模型。</p></div><button className="outline-button" onClick={onCreate}><Plus size={17} weight="bold" />添加模型</button></section>{editorOpen && <section className="profile-editor model-editor"><div className="panel-title editor-heading"><div><p className="eyebrow">{editingId ? "EDIT MODEL" : "NEW MODEL"}</p><h2>{editingId ? "编辑模型配置" : "添加模型配置"}</h2><span>API Key 只会发送给本地 event-center 并加密保存。</span></div><button className="text-button editor-close" onClick={onClose} type="button">关闭</button></div><form className="profile-form model-form" onSubmit={onSubmit}><label>配置名称<input value={draft.name} onChange={(event) => onDraftChange({ ...draft, name: event.target.value })} placeholder="例如：日常默认模型" required /></label><label>模型名称<input value={draft.model} onChange={(event) => onDraftChange({ ...draft, model: event.target.value })} placeholder="例如：gpt-4.1-mini" required /></label><label>Provider<select value={draft.provider} onChange={(event) => onDraftChange({ ...draft, provider: event.target.value })}><option value="OPENAI_COMPATIBLE">OpenAI Compatible</option></select></label><label>Base URL<input value={draft.baseUrl} onChange={(event) => onDraftChange({ ...draft, baseUrl: event.target.value })} placeholder="https://api.openai.com/v1" required /></label><label className="full">API Key<div className="secret-input"><Key size={17} /><input type="password" autoComplete="off" value={draft.apiKey} onChange={(event) => onDraftChange({ ...draft, apiKey: event.target.value })} placeholder={editingId ? "留空则保留原密钥" : "输入模型服务 API Key"} required={!editingId} /></div></label><label>Temperature<input type="number" min="0" max="2" step="0.1" value={draft.temperature} onChange={(event) => onDraftChange({ ...draft, temperature: Number(event.target.value) })} /></label><label>最大输出 Tokens<input type="number" min="1" step="1" value={draft.maxTokens} onChange={(event) => onDraftChange({ ...draft, maxTokens: Number(event.target.value) })} /></label><label>优先级<input type="number" step="1" value={draft.priority} onChange={(event) => onDraftChange({ ...draft, priority: Number(event.target.value) })} /></label><div className="toggle-group"><label><input type="checkbox" checked={draft.enabled} onChange={(event) => onDraftChange({ ...draft, enabled: event.target.checked })} /><span>启用配置</span></label><label><input type="checkbox" checked={draft.isDefault} onChange={(event) => onDraftChange({ ...draft, isDefault: event.target.checked })} /><span>设为默认</span></label></div><label className="full">适用 Route <small>不选择表示全局可用</small><div className="route-selector">{MODEL_ROUTES.map((route) => <button className={draft.supportedRoutes.includes(route) ? "selected" : ""} key={route} type="button" onClick={() => toggleRoute(route)}>{route}</button>)}</div></label><label className="full">说明<textarea value={draft.description} onChange={(event) => onDraftChange({ ...draft, description: event.target.value })} placeholder="记录这个模型适合处理的任务和使用边界。" /></label><div className="form-action full"><small>{editingId ? "API Key 留空时会继续使用当前已加密保存的密钥。" : "密钥不会在创建后的任何客户端响应中明文返回。"}</small><button disabled={busy} type="submit">{busy ? "正在保存…" : editingId ? "保存修改" : "创建配置"}</button></div></form></section>}<section className="model-list">{profiles.length === 0 ? <div className="model-empty"><Cpu size={25} /><h3>还没有模型配置</h3><p>添加一个 OpenAI Compatible 模型后，首页 Agent 才能生成智能回复。</p><button onClick={onCreate}>添加第一个模型</button></div> : profiles.map((profile) => <article className="model-card" key={profile.id}><div className="model-icon"><Cpu size={20} /></div><div className="model-main"><div className="model-title"><h3>{profile.name}</h3>{profile.isDefault && <span>默认</span>}{!profile.enabled && <span className="muted-badge">已停用</span>}</div><p>{profile.description || "未填写配置说明"}</p><div className="model-meta"><span>{profile.provider}</span><span>{profile.model || "未指定模型"}</span><span>{profile.hasApiKey ? profile.apiKeyMasked : "未配置 Key"}</span></div><div className="model-routes">{profile.supportedRoutes.length === 0 ? <span>全部 route</span> : profile.supportedRoutes.map((route) => <span key={route}>{route}</span>)}</div></div><div className="model-actions"><button title="编辑配置" onClick={() => onEdit(profile)}><PencilSimple size={17} /></button><button className="danger-action" title="删除配置" onClick={() => onDelete(profile)}><Trash size={17} /></button></div></article>)}</section></div>;
}

/** 将会话设定的作用范围压缩成适合列表展示的一句话。 */
function describeScope(profile: ConversationProfile) {
  const target = profile.chatIds.length > 0 ? `${profile.chatIds.length} 个指定会话` : profile.chatType === "private" ? "全部私聊" : profile.chatType === "group" ? "全部群聊" : "全部会话";
  return `${profile.platform || "全部平台"} · ${target}`;
}

/** 将 Skill 引用或人格模式转换为用户可快速理解的规则标签。 */
function describePersona(profile: ConversationProfile) {
  return profile.personaMode !== "NONE" ? `${profile.personaMode} 人格` : profile.description || "未加载 Skill 或人格提示";
}

/** 汇总触发条件、回复方式和通知策略，形成草图中的回复策略字段。 */
function describeReplyStrategy(profile: ConversationProfile) {
  const trigger = profile.replyMode === "AUTO_REPLY" ? "自动回复" : profile.replyMode || "仅建议";
  return `${trigger} · ${profile.notificationMode || "AUTO"} 通知`;
}

/** 将字节数转换成便于用户理解的下载容量。 */
function formatTransferBytes(bytes: number) {
  if (!Number.isFinite(bytes) || bytes <= 0) return "0 MB";
  return `${(bytes / 1024 / 1024).toFixed(bytes >= 10 * 1024 * 1024 ? 1 : 2)} MB`;
}

/** 渲染以扫码为主的 NapCat 连接页，手工端口和 Token 仅保留为高级排障入口。 */
function Connections({ connections, draft, qrLogin, qrLoginOpen, qrLoginBusy, napcatRuntime, napcatInstallProgress, napcatLicenseAccepted, onNapcatLicenseAccepted, onPrepareNapcatRuntime, onOpenQrLogin, onCloseQrLogin, onRefreshQrLogin, onDraftChange, onSubmit, onCheck, busy }: { connections: PlatformConnection[]; draft: PlatformConnectionDraft; qrLogin: NapcatQrLoginState | null; qrLoginOpen: boolean; qrLoginBusy: boolean; napcatRuntime: NapcatRuntimeStatus | null; napcatInstallProgress: NapcatInstallProgress | null; napcatLicenseAccepted: boolean; onNapcatLicenseAccepted: (accepted: boolean) => void; onPrepareNapcatRuntime: () => void; onOpenQrLogin: () => void; onCloseQrLogin: () => void; onRefreshQrLogin: () => void; onDraftChange: (value: PlatformConnectionDraft) => void; onSubmit: (event: FormEvent) => void; onCheck: (id: string) => void; busy: boolean }) {
  const qqConnection = connections.find((item) => item.platform === "qq");
  const connected = Boolean(qqConnection?.connected);
  const runtimeSetupRequired = Boolean(napcatRuntime && !napcatRuntime.ready && !qrLogin?.qrCodeUrl && qrLogin?.state !== "CONNECTED");
  const showInstallProgress = Boolean(napcatInstallProgress && napcatInstallProgress.state !== "IDLE");
  const transferDetail = napcatInstallProgress
    ? `${formatTransferBytes(napcatInstallProgress.downloadedBytes)} / ${formatTransferBytes(napcatInstallProgress.totalBytes)}${napcatInstallProgress.bytesPerSecond > 0 ? ` · ${formatTransferBytes(napcatInstallProgress.bytesPerSecond)}/s` : ""}`
    : "";

  return <div className="connection-center">
    <section className="settings-header">
      <div><p className="eyebrow">CONNECT QQ</p><h2>连接 QQ</h2><p>只需扫码。Memo Echo 会准备本机运行时并自动完成消息收发配置。</p></div>
      <a className="outline-button" href="https://napneko.github.io/guide/boot/Shell" target="_blank" rel="noreferrer">官方说明<ArrowSquareOut size={15} /></a>
    </section>
    <section className="napcat-quick-connect">
      <div className="napcat-symbol">N</div>
      <div className="quick-connect-copy"><span>{connected ? "QQ 已连接" : "本机 NapCat"}</span><h3>{connected ? qqConnection?.accountName || qqConnection?.accountId : "扫码连接你的 QQ"}</h3><p>{connected ? `账号 ${qqConnection?.accountId || "已识别"}，消息接收与 API 调用均已启用` : "无需打开 NapCat 网络配置，也无需手工复制 Token"}</p></div>
      <button className={connected ? "qr-secondary-button" : "qr-primary-button"} type="button" onClick={onOpenQrLogin}>{connected ? "重新登录" : "扫码连接 QQ"}</button>
    </section>
    <div className="connection-prerequisite"><ShieldCheck size={17} /><div><b>NapCat 由 Memo Echo 在本机托管</b><p>首次使用下载官方 Windows Node 运行时并校验 SHA-256；之后会自动隐藏启动。已有的本机实例也可以直接复用。</p></div></div>
    <details className="connection-advanced"><summary>高级手工配置</summary><div><p>仅自定义端口、远程部署或自动发现失败时使用。</p><LegacyConnections connections={connections} draft={draft} onDraftChange={onDraftChange} onSubmit={onSubmit} onCheck={onCheck} busy={busy} /></div></details>

    {qrLoginOpen && <div className="qr-login-backdrop" role="presentation" onMouseDown={(event) => { if (event.currentTarget === event.target) onCloseQrLogin(); }}>
      <section className="qr-login-dialog" role="dialog" aria-modal="true" aria-label="QQ 扫码登录">
        <button className="qr-close" type="button" onClick={onCloseQrLogin}>关闭</button>
        <div className="qr-dialog-heading">
          <p className="eyebrow">QQ SIGN IN</p>
          <h2>{qrLogin?.state === "CONNECTED" ? "连接完成" : runtimeSetupRequired ? "准备 QQ 连接组件" : "使用手机 QQ 扫码"}</h2>
          <span>{qrLogin?.message || (qrLoginBusy ? "正在向 NapCat 获取二维码…" : "正在准备扫码登录")}</span>
        </div>
        {runtimeSetupRequired ? <div className="napcat-runtime-setup">
          <div className="runtime-version"><DownloadSimple size={20} /><div><b>NapCat {napcatRuntime?.version}</b><span>约 110 MB，仅从 NapCatQQ 官方 GitHub Release 下载</span></div></div>
          <ul><li>校验官方发布资产的 SHA-256</li><li>安全解压到 Memo Echo 本地数据目录</li><li>后台启动并等待 WebUI 就绪</li></ul>
          {showInstallProgress && napcatInstallProgress && <div className={`runtime-progress ${napcatInstallProgress.state === "FAILED" ? "failed" : ""}`}>
            <div className="runtime-progress-heading"><b>{napcatInstallProgress.message}</b><strong>{Math.round(napcatInstallProgress.percent)}%</strong></div>
            <div className="runtime-progress-track"><span style={{ width: `${napcatInstallProgress.percent}%` }} /></div>
            <div className="runtime-progress-meta"><span>{napcatInstallProgress.state === "DOWNLOADING" ? transferDetail : "下载完成后会自动校验并解压"}</span><span>{napcatInstallProgress.error}</span></div>
          </div>}
          <label className="runtime-license"><input type="checkbox" checked={napcatLicenseAccepted} onChange={(event) => onNapcatLicenseAccepted(event.target.checked)} /><span>我已阅读并同意 <a href={napcatRuntime?.licenseUrl} target="_blank" rel="noreferrer">NapCat 第三方许可</a>，了解它并非 Memo Echo 的组成部分</span></label>
          <button type="button" disabled={!napcatLicenseAccepted || qrLoginBusy} onClick={onPrepareNapcatRuntime}>{qrLoginBusy ? <><ArrowClockwise className="spinning" size={15} />{napcatInstallProgress?.message || "正在准备"}</> : napcatRuntime?.installed ? "启动并获取二维码" : napcatInstallProgress?.state === "FAILED" ? "重试安装" : "安装并获取二维码"}</button>
        </div> : <>
          <div className={`qr-code-frame ${qrLogin?.state === "CONNECTED" ? "connected" : ""}`}>
            {qrLoginBusy && !qrLogin && <ArrowClockwise className="spinning" size={34} />}
            {qrLogin?.qrCodeUrl && <img src={qrLogin.qrCodeUrl} alt="QQ 登录二维码" />}
            {qrLogin?.state === "CONNECTED" && <CheckCircle size={70} weight="fill" />}
            {qrLogin && !qrLogin.qrCodeUrl && qrLogin.state !== "CONNECTED" && (
              <span>{qrLogin.state === "RESTORING" ? "正在恢复上次登录" : "二维码暂不可用"}</span>
            )}
          </div>
          {qrLogin?.state === "CONNECTED" ? <div className="qr-account"><b>{qrLogin.accountName || "QQ 用户"}</b><span>{qrLogin.accountId}</span><button type="button" onClick={onCloseQrLogin}>进入工作台</button></div> : <div className="qr-dialog-actions"><span>{qrLogin?.state === "RESTORING" ? "正在复用本机 QQ 登录缓存，无需重复扫码" : "扫码后在手机上确认登录，本页会自动完成后续配置"}</span><button type="button" disabled={qrLoginBusy || qrLogin?.state === "RESTORING"} onClick={onRefreshQrLogin}><ArrowClockwise size={15} />刷新二维码</button></div>}
        </>}
      </section>
    </div>}
  </div>;
}

/** 渲染平台连接表单和当前连接的健康检测操作。 */
function LegacyConnections({ connections, draft, onDraftChange, onSubmit, onCheck, busy }: { connections: PlatformConnection[]; draft: PlatformConnectionDraft; onDraftChange: (value: PlatformConnectionDraft) => void; onSubmit: (event: FormEvent) => void; onCheck: (id: string) => void; busy: boolean }) {
  return <><section className="panel"><div className="panel-title"><h2>新增平台连接</h2><span>凭据仅发送给后端加密保存</span></div><form className="connection-form" onSubmit={onSubmit}><label>名称<input value={draft.name} onChange={(event) => onDraftChange({ ...draft, name: event.target.value })} required /></label><label>平台<input value={draft.platform} onChange={(event) => onDraftChange({ ...draft, platform: event.target.value })} required /></label><label>连接器<input value={draft.connector} onChange={(event) => onDraftChange({ ...draft, connector: event.target.value })} required /></label><label>连接器地址<input value={draft.connectorBaseUrl} onChange={(event) => onDraftChange({ ...draft, connectorBaseUrl: event.target.value })} /></label><label>凭据 / Token<input type="password" value={draft.credential} onChange={(event) => onDraftChange({ ...draft, credential: event.target.value })} /></label><button disabled={busy} type="submit">保存连接</button></form></section><section className="panel"><div className="panel-title"><h2>已有连接</h2><span>{connections.length} 条记录</span></div>{connections.length === 0 ? <p className="empty">还没有保存的平台连接。</p> : connections.map((item) => <div className="connection-action" key={item.id}><ConnectionRow item={item} /><button onClick={() => onCheck(item.id)} disabled={busy}>检测</button></div>)}</section></>;
}

/** 以统一视觉显示一条平台连接的账户和健康状态。 */
function ConnectionRow({ item }: { item: PlatformConnection }) {
  return <div className="connection"><b>{item.name}</b><span>{item.platform} · {item.accountName || item.accountId || "未登录"}</span><i className={item.connected ? "online" : "offline"}>{item.connected ? "已连接" : item.health || "待处理"}</i></div>;
}
