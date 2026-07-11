import { FormEvent, useEffect, useState } from "react";
import {
  ArrowClockwise,
  Brain,
  CalendarDots,
  ChatCircleDots,
  Cpu,
  DotsThree,
  FileText,
  GithubLogo,
  HardDrives,
  House,
  Key,
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
  createConnection,
  createConversationProfile,
  createModelProfile,
  deleteModelProfile,
  executeWorkspaceCommand,
  installGithubSkill,
  listConnections,
  listConversationProfiles,
  listConversations,
  listModelProfiles,
  listSkills,
  login,
  register,
  searchQqContacts,
  updateModelProfile,
} from "./api/client";
import { loadCredential, removeCredential, saveCredential } from "./api/secure-store";
import type {
  ConversationProfile,
  ConversationSummary,
  ModelProfile,
  ModelProfileDraft,
  PlatformConnection,
  PlatformConnectionDraft,
  QqContact,
  SkillDescriptor,
  StoredCredential,
  WorkspaceCommandResponse,
} from "./types";

const DEFAULT_BASE_URL = "http://127.0.0.1:8093";
const EMPTY_CONNECTION: PlatformConnectionDraft = {
  name: "本地 QQ / NapCat",
  platform: "qq",
  connector: "qq-napcat",
  connectorBaseUrl: "http://127.0.0.1:8091",
  credential: "",
};

type View = "dashboard" | "messages" | "profiles" | "models" | "connections";

const VIEW_LABELS: Record<View, string> = {
  dashboard: "今日脉搏",
  messages: "消息空间",
  profiles: "设定集",
  models: "模型配置",
  connections: "连接管理",
};

const MODEL_ROUTES = [
  "social_reply",
  "chat_summary",
  "task_plan",
  "schedule_extract",
  "file_analysis",
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

type ConversationProfileDraft = {
  name: string;
  chatType: string;
  contactIds: string[];
  systemPrompt: string;
  replyMode: string;
  skillMode: "prompt" | "local" | "github";
  skillReference: string;
  githubReference: string;
};

const EMPTY_PROFILE: ConversationProfileDraft = {
  name: "", chatType: "private", contactIds: [], systemPrompt: "", replyMode: "DRAFT_ONLY",
  skillMode: "prompt", skillReference: "", githubReference: "",
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

/** 渲染登录、注册、消息、设定集和连接管理页面的桌面客户端根组件。 */
export function App() {
  const [credential, setCredential] = useState<StoredCredential | null>(null);
  const [baseUrl, setBaseUrl] = useState(DEFAULT_BASE_URL);
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [displayName, setDisplayName] = useState("");
  const [registerMode, setRegisterMode] = useState(false);
  const [activeView, setActiveView] = useState<View>("dashboard");
  const [connections, setConnections] = useState<PlatformConnection[]>([]);
  const [modelProfiles, setModelProfiles] = useState<ModelProfile[]>([]);
  const [modelDraft, setModelDraft] = useState<ModelProfileDraft>(EMPTY_MODEL);
  const [editingModelId, setEditingModelId] = useState("");
  const [modelEditorOpen, setModelEditorOpen] = useState(false);
  const [conversations, setConversations] = useState<ConversationSummary[]>([]);
  const [conversationProfiles, setConversationProfiles] = useState<ConversationProfile[]>([]);
  const [connectionDraft, setConnectionDraft] = useState<PlatformConnectionDraft>(EMPTY_CONNECTION);
  const [profileDraft, setProfileDraft] = useState<ConversationProfileDraft>(EMPTY_PROFILE);
  const [profileEditorOpen, setProfileEditorOpen] = useState(false);
  const [qqContacts, setQqContacts] = useState<QqContact[]>([]);
  const [skills, setSkills] = useState<SkillDescriptor[]>([]);
  const [contactKeyword, setContactKeyword] = useState("");
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

  /** 根据页面类型读取后端数据，并统一更新加载状态和可读错误消息。 */
  async function loadView(view: View, currentCredential: StoredCredential) {
    setBusy(true);
    try {
      if (view === "messages") {
        setConversations(await listConversations(currentCredential));
      } else if (view === "profiles") {
        const [nextProfiles, nextModels] = await Promise.all([
          listConversationProfiles(currentCredential), listModelProfiles(currentCredential),
        ]);
        setConversationProfiles(nextProfiles);
        setModelProfiles(nextModels);
      } else if (view === "models") {
        setModelProfiles(await listModelProfiles(currentCredential));
      } else {
        const [nextConnections, nextModels] = await Promise.all([
          listConnections(currentCredential), listModelProfiles(currentCredential),
        ]);
        setConnections(nextConnections);
        setModelProfiles(nextModels);
      }
      setStatus("服务已连接，数据已刷新");
    } catch (error) {
      setStatus(error instanceof Error ? error.message : "读取客户端数据失败");
    } finally {
      setBusy(false);
    }
  }

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

  /** 保存新建会话设定，并用后端返回的规则刷新当前设定集页面。 */
  async function submitConversationProfile(event: FormEvent) {
    event.preventDefault();
    if (!credential) return;
    setBusy(true);
    try {
      const skillReference = await resolveSkillReference();
      await createConversationProfile(credential, {
        name: profileDraft.name,
        description: profileDraft.systemPrompt,
        enabled: true,
        platform: "qq",
        accountId: "",
        scene: "",
        chatType: profileDraft.chatType,
        chatIds: profileDraft.contactIds,
        targetUserIds: [],
        supportedRoutes: ["social_reply"],
        triggerMode: profileDraft.chatType === "group" ? "AT_SELF_ONLY" : "ALWAYS",
        triggerKeywords: [],
        personaMode: profileDraft.skillMode === "prompt" && profileDraft.systemPrompt.trim() ? "PROMPT" : "NONE",
        systemPrompt: profileDraft.skillMode === "prompt" ? profileDraft.systemPrompt : "",
        skillReference,
        skillReferences: skillReference ? [skillReference] : [],
        modelProfileId: "",
        preferredRoute: "social_reply",
        replyMode: profileDraft.replyMode,
        allowedTools: [],
        requireHumanConfirmation: profileDraft.replyMode !== "AUTO_REPLY",
        priority: 10,
        notificationMode: "AUTO",
        notificationKeywords: [],
        includeUrgentInDigest: false,
      });
      setProfileDraft(EMPTY_PROFILE);
      setProfileEditorOpen(false);
      await loadView("profiles", credential);
      setStatus("会话设定已保存。");
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

  /**
   * 根据选择的 Skill 来源返回可保存的引用；GitHub 来源会先安装到本地 Skill 目录。
   */
  async function resolveSkillReference() {
    if (!credential || profileDraft.skillMode === "prompt") return "";
    if (profileDraft.skillMode === "local") return profileDraft.skillReference;
    if (!profileDraft.githubReference.trim()) throw new Error("请填写 GitHub Skill 引用。");
    const result = await installGithubSkill(credential, profileDraft.githubReference.trim());
    setSkills((current) => [result.descriptor, ...current.filter((item) => item.reference !== result.descriptor.reference)]);
    return result.descriptor.reference;
  }

  /**
   * 打开新建设定编辑器，同时拉取可搜索的 QQ 联系人与本地 Skill 清单。
   */
  async function openProfileEditor() {
    if (!credential) return;
    setProfileEditorOpen(true);
    setBusy(true);
    try {
      const [contacts, nextSkills] = await Promise.all([searchQqContacts(credential, ""), listSkills(credential)]);
      setQqContacts(contacts);
      setSkills(nextSkills);
    } catch (error) {
      setStatus(error instanceof Error ? error.message : "读取联系人或 Skill 失败");
    } finally {
      setBusy(false);
    }
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

  return (
    <main className="desktop-shell">
      <aside className="app-sidebar">
        <div className="sidebar-brand">
          <span className="brand-symbol">M</span>
          <div><strong>Memo Echo</strong><small>Personal agent</small></div>
        </div>
        <button className="new-task-button" onClick={() => setActiveView("dashboard")} type="button">
          <Plus size={18} weight="bold" />新建任务
        </button>
        <nav className="primary-nav" aria-label="主导航">
          <p>工作台</p>
          <button className={activeView === "dashboard" ? "active" : ""} onClick={() => setActiveView("dashboard")}><House size={19} /><span>今日脉搏</span></button>
          <button className={activeView === "messages" ? "active" : ""} onClick={() => setActiveView("messages")}><ChatCircleDots size={19} /><span>消息空间</span></button>
          <button className={activeView === "profiles" ? "active" : ""} onClick={() => setActiveView("profiles")}><SlidersHorizontal size={19} /><span>设定集</span></button>
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
          {activeView === "dashboard" && <Dashboard connections={connections} models={modelProfiles} status={status} onExecute={runWorkspaceCommand} onOpenModels={() => setActiveView("models")} onOpenProfiles={() => setActiveView("profiles")} onOpenConnections={() => setActiveView("connections")} />}
          {activeView === "messages" && <Messages conversations={conversations} status={status} />}
          {activeView === "profiles" && <><ProfileComposer open={profileEditorOpen} draft={profileDraft} onDraftChange={setProfileDraft} onSubmit={submitConversationProfile} onClose={() => setProfileEditorOpen(false)} busy={busy} contacts={qqContacts} contactKeyword={contactKeyword} onContactKeywordChange={setContactKeyword} skills={skills} /><Profiles profiles={conversationProfiles} models={modelProfiles} onCreate={openProfileEditor} /></>}
          {activeView === "models" && <Models profiles={modelProfiles} draft={modelDraft} editorOpen={modelEditorOpen} editingId={editingModelId} busy={busy} onCreate={() => openModelEditor()} onEdit={openModelEditor} onDelete={(profile) => void removeModelProfile(profile)} onDraftChange={setModelDraft} onSubmit={submitModelProfile} onClose={closeModelEditor} />}
          {activeView === "connections" && <Connections connections={connections} draft={connectionDraft} onDraftChange={setConnectionDraft} onSubmit={submitConnection} onCheck={refreshConnection} busy={busy} />}
        </div>
      </section>
    </main>
  );
}

/** 渲染更接近桌面 Agent 的首页：能力入口、指令输入和环境状态分层展示。 */
function Dashboard({ connections, models, status, onExecute, onOpenModels, onOpenProfiles, onOpenConnections }: { connections: PlatformConnection[]; models: ModelProfile[]; status: string; onExecute: (prompt: string, requestedRoute: string) => Promise<WorkspaceCommandResponse>; onOpenModels: () => void; onOpenProfiles: () => void; onOpenConnections: () => void }) {
  const [mode, setMode] = useState("assistant");
  const [prompt, setPrompt] = useState("");
  const [composerNotice, setComposerNotice] = useState("");
  const [requestedRoute, setRequestedRoute] = useState("");
  const [executing, setExecuting] = useState(false);
  const [commandResult, setCommandResult] = useState<WorkspaceCommandResponse | null>(null);
  const defaultModel = models.find((item) => item.isDefault)?.model || "选择模型";
  const onlineConnections = connections.filter((item) => item.connected).length;
  const capabilities = [
    { label: "文档处理", prompt: "帮我解析并整理一份文档", route: "file_analysis", icon: <FileText size={18} /> },
    { label: "群聊摘要", prompt: "总结我离开期间的重要群聊消息", route: "chat_summary", icon: <UsersThree size={18} /> },
    { label: "日程规划", prompt: "根据最近消息规划今天的日程", route: "schedule_extract", icon: <CalendarDots size={18} /> },
    { label: "更多能力", prompt: "展示当前可以使用的能力", route: "", icon: <DotsThree size={18} /> },
  ];

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
      setComposerNotice(result.status === "success" ? "Agent 已完成本次任务。" : result.error || "Agent 执行失败。");
    } catch (error) {
      const message = error instanceof Error ? error.message : "Agent 执行失败。";
      setCommandResult({ commandId: "", status: "failed", route: requestedRoute, summary: "", finalReply: "", needConfirmation: false, results: [], error: message });
      setComposerNotice(message);
    } finally {
      setExecuting(false);
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
      {commandResult && <section className={`command-result ${commandResult.status === "success" ? "command-success" : "command-failed"}`}><div className="command-result-heading"><span><Sparkle size={16} weight="fill" />{commandResult.status === "success" ? "执行完成" : "执行失败"}</span><small>{commandResult.route || requestedRoute || "auto_route"}</small></div><p>{commandResult.finalReply || commandResult.error || commandResult.summary || "Agent 没有返回可展示的文本结果。"}</p>{commandResult.results.length > 0 && <div className="command-agents">{commandResult.results.map((item, index) => <span key={`${item.agent}-${index}`}>{item.agent}<i>{item.status}</i></span>)}</div>}{commandResult.needConfirmation && <div className="command-warning"><ShieldCheck size={15} />本次结果需要你确认后才能执行外部操作。</div>}</section>}
    </div>
  );
}

function LegacyDashboard({ connections, models, status }: { connections: PlatformConnection[]; models: ModelProfile[]; status: string }) {
  return <><div className="metrics"><article><span>平台连接</span><strong>{connections.length}</strong><small>{connections.filter((item) => item.connected).length} 个在线</small></article><article><span>模型配置</span><strong>{models.length}</strong><small>{models.filter((item) => item.enabled).length} 个启用</small></article><article><span>默认模型</span><strong>{models.find((item) => item.isDefault)?.model || "未设置"}</strong><small>用于智能处理</small></article></div><section className="panel"><div className="panel-title"><h2>接入状态</h2><span>{status}</span></div>{connections.length === 0 ? <p className="empty">尚未配置平台连接。请到“连接管理”接入 NapCat。</p> : connections.map((item) => <ConnectionRow key={item.id} item={item} />)}</section></>;
}

/** 渲染最近会话摘要，并提示下一步将支持会话详情和操作。 */
function Messages({ conversations, status }: { conversations: ConversationSummary[]; status: string }) {
  return <section className="panel"><div className="panel-title"><h2>最近会话</h2><span>{status}</span></div>{conversations.length === 0 ? <p className="empty">尚未收到可展示的会话事件。连接 NapCat 后，消息会在这里出现。</p> : conversations.map((item) => <article className="message-card" key={`${item.platform}-${item.chatId}`}><div><b>{item.chatName || item.chatId}</b><span>{item.platform} · {item.chatType} · {formatTime(item.lastMessageTime)}</span></div><p>{item.lastSenderName ? `${item.lastSenderName}：` : ""}{item.lastMessage || "暂无文本消息"}</p><i className={item.actionRequired ? "online" : "offline"}>{item.actionRequired ? "需要处理" : "已归档"}</i></article>)}</section>;
}

/** 渲染横向设定集规则清单，让会话范围、人格和回复策略一眼可见。 */
/** 渲染新建会话设定的编辑器，并支持联系人搜索与多种 Skill 来源。 */
function ProfileComposer({ open, draft, onDraftChange, onSubmit, onClose, busy, contacts, contactKeyword, onContactKeywordChange, skills }: { open: boolean; draft: ConversationProfileDraft; onDraftChange: (draft: ConversationProfileDraft) => void; onSubmit: (event: FormEvent) => void; onClose: () => void; busy: boolean; contacts: QqContact[]; contactKeyword: string; onContactKeywordChange: (value: string) => void; skills: SkillDescriptor[] }) {
  if (!open) return null;
  const visibleContacts = contacts.filter((item) => item.type === draft.chatType && `${item.name} ${item.remark} ${item.id}`.toLowerCase().includes(contactKeyword.trim().toLowerCase()));
  const selectedContacts = contacts.filter((item) => draft.contactIds.includes(item.id));

  /** 添加或移除一条设定所绑定的 QQ 好友或群聊。 */
  const toggleContact = (contactId: string) => onDraftChange({ ...draft, contactIds: draft.contactIds.includes(contactId) ? draft.contactIds.filter((item) => item !== contactId) : [...draft.contactIds, contactId] });
  return (
    <section className="profile-editor">
      <div className="panel-title editor-heading">
        <div><p className="eyebrow">NEW CONVERSATION RULE</p><h2>新建设定</h2><span>为指定会话单独设置人格、Skill 和回复边界。</span></div>
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
        <label className="full">Skill 来源
          <div className="skill-modes">
            <button type="button" className={draft.skillMode === "prompt" ? "selected" : ""} onClick={() => onDraftChange({ ...draft, skillMode: "prompt", skillReference: "", githubReference: "" })}><FileText size={18} /><span><b>只设置提示词</b><small>直接定义这个会话的人格与边界</small></span></button>
            <button type="button" className={draft.skillMode === "local" ? "selected" : ""} onClick={() => onDraftChange({ ...draft, skillMode: "local", githubReference: "" })}><HardDrives size={18} /><span><b>载入本地 Skill</b><small>使用已经安装并审核过的能力</small></span></button>
            <button type="button" className={draft.skillMode === "github" ? "selected" : ""} onClick={() => onDraftChange({ ...draft, skillMode: "github", skillReference: "" })}><GithubLogo size={18} /><span><b>从 GitHub 安装</b><small>通过仓库引用安装开源 Skill</small></span></button>
          </div>
        </label>
        {draft.skillMode === "prompt" && <label className="full">人格提示<textarea value={draft.systemPrompt} onChange={(event) => onDraftChange({ ...draft, systemPrompt: event.target.value })} placeholder="例如：语气自然、简短；如涉及承诺、转账或敏感事项，只生成草稿。" /></label>}
        {draft.skillMode === "local" && <label className="full">选择本地 Skill<select value={draft.skillReference} onChange={(event) => onDraftChange({ ...draft, skillReference: event.target.value })}><option value="">请选择已安装的 Skill</option>{skills.map((skill) => <option key={skill.reference} value={skill.reference}>{skill.name} · {skill.sourceType}</option>)}</select></label>}
        {draft.skillMode === "github" && <label className="full">GitHub Skill 引用<input value={draft.githubReference} onChange={(event) => onDraftChange({ ...draft, githubReference: event.target.value })} placeholder="例如：github://owner/repo/path/to/skill.json" /></label>}
        <label>回复策略<select value={draft.replyMode} onChange={(event) => onDraftChange({ ...draft, replyMode: event.target.value })}><option value="DRAFT_ONLY">只生成草稿，等待确认</option><option value="AUTO_REPLY">自动回复</option></select></label>
        <div className="form-action"><small>自动回复属于高风险操作，建议先使用草稿模式验证设定。</small><button disabled={busy} type="submit">{busy ? "正在保存…" : "保存设定"}</button></div>
      </form>
    </section>
  );
}

/** 渲染横向设定集规则清单，让会话范围、人格和回复策略一眼可见。 */
function Profiles({ profiles, models, onCreate }: { profiles: ConversationProfile[]; models: ModelProfile[]; onCreate: () => void }) {
  return <div className="settings-page"><section className="settings-header"><div><p className="eyebrow">CONVERSATION RULES</p><h2>设定集</h2><p>为不同会话定义人格、Skill 和自动回复边界。</p></div><button className="outline-button" onClick={onCreate}><Plus size={17} weight="bold" />新建设定</button></section><section className="settings-board"><div className="settings-labels"><span>设定名称</span><span>生效会话</span><span>Skill / 人格提示</span><span>回复策略</span></div>{profiles.length === 0 ? <p className="empty settings-empty">尚未创建会话设定。你可以先为一个私聊或群聊定义回复规则。</p> : profiles.map((item) => <article className="setting-row" key={item.id}><div className="setting-name"><b>{item.name}</b><small>{item.enabled ? "已启用" : "已停用"} · 优先级 {item.priority}</small></div><span className="rule-chip">{describeScope(item)}</span><span className="rule-chip">{describePersona(item)}</span><span className="rule-chip wide">{describeReplyStrategy(item)}</span></article>)}</section><section className="model-strip"><div><h3>模型配置</h3><p>当前模型仅用于本地 Agent 调用，密钥始终不会显示在客户端。</p></div><div className="model-pills">{models.length === 0 ? <span className="rule-chip">尚未配置模型</span> : models.map((item) => <span className="rule-chip" key={item.id}>{item.isDefault ? "默认 · " : ""}{item.name} / {item.model}</span>)}</div></section></div>;
}

/** 渲染用户模型配置中心，提供加密密钥录入、route 绑定和默认模型管理。 */
function Models({ profiles, draft, editorOpen, editingId, busy, onCreate, onEdit, onDelete, onDraftChange, onSubmit, onClose }: { profiles: ModelProfile[]; draft: ModelProfileDraft; editorOpen: boolean; editingId: string; busy: boolean; onCreate: () => void; onEdit: (profile: ModelProfile) => void; onDelete: (profile: ModelProfile) => void; onDraftChange: (draft: ModelProfileDraft) => void; onSubmit: (event: FormEvent) => void; onClose: () => void }) {
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

/** 渲染平台连接创建表单和当前连接的健康检测操作。 */
function Connections({ connections, draft, onDraftChange, onSubmit, onCheck, busy }: { connections: PlatformConnection[]; draft: PlatformConnectionDraft; onDraftChange: (value: PlatformConnectionDraft) => void; onSubmit: (event: FormEvent) => void; onCheck: (id: string) => void; busy: boolean }) {
  return <><section className="panel"><div className="panel-title"><h2>新增平台连接</h2><span>凭据仅发送给后端加密保存</span></div><form className="connection-form" onSubmit={onSubmit}><label>名称<input value={draft.name} onChange={(event) => onDraftChange({ ...draft, name: event.target.value })} required /></label><label>平台<input value={draft.platform} onChange={(event) => onDraftChange({ ...draft, platform: event.target.value })} required /></label><label>连接器<input value={draft.connector} onChange={(event) => onDraftChange({ ...draft, connector: event.target.value })} required /></label><label>连接器地址<input value={draft.connectorBaseUrl} onChange={(event) => onDraftChange({ ...draft, connectorBaseUrl: event.target.value })} /></label><label>凭据 / Token<input type="password" value={draft.credential} onChange={(event) => onDraftChange({ ...draft, credential: event.target.value })} /></label><button disabled={busy} type="submit">保存连接</button></form></section><section className="panel"><div className="panel-title"><h2>已有连接</h2><span>{connections.length} 条记录</span></div>{connections.length === 0 ? <p className="empty">还没有保存的平台连接。</p> : connections.map((item) => <div className="connection-action" key={item.id}><ConnectionRow item={item} /><button onClick={() => onCheck(item.id)} disabled={busy}>检测</button></div>)}</section></>;
}

/** 以统一视觉显示一条平台连接的账户和健康状态。 */
function ConnectionRow({ item }: { item: PlatformConnection }) {
  return <div className="connection"><b>{item.name}</b><span>{item.platform} · {item.accountName || item.accountId || "未登录"}</span><i className={item.connected ? "online" : "offline"}>{item.connected ? "已连接" : item.health || "待处理"}</i></div>;
}
