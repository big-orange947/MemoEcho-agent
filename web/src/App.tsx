/* =============================================================================
 * App.tsx - 应用外壳
 * -----------------------------------------------------------------------------
 * 布局：左侧列表（随标签页切换形态）/ 中间主区（六个标签页）/ 右侧检查器 /
 *       底部状态栏。
 *
 * 两个空间，别搞混：
 *   · 控制台 —— 你和 agent 的**对话**。用自然语言派活，agent 自己去调工具、
 *     发消息，执行过程内嵌在对话里。左侧列的是"对话"(线程)。
 *   · 会话   —— agent 替你**盯着**的 QQ 好友/群。看消息流、改值守策略。
 *     左侧列的是"值守会话"。
 *
 * 数据流刻意保持"笨"：
 *   一个 refreshTick 计数器 + 各屏自己拉数据。SSE 事件到达就把 tick +1，
 *   谁需要刷新谁重拉。唯一的例外是控制台的执行轨迹 —— 它是流式追加的，
 *   由这里的 liveRun 状态维护(step 追加一步、run 更新状态)，再交给控制台渲染；
 *   这样"正在跑"的过程不用等重拉，界面也不会闪。
 * ========================================================================== */

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import {
  Conversation,
  Run,
  Thread,
  createThread,
  listConversations,
  listThreads,
} from "./lib/api";
import { subscribe } from "./lib/sse";
import { Sidebar, ThreadSidebar } from "./components/Sidebar";
import { ConsoleInspector, ConsoleScreen } from "./screens/ConsoleScreen";
import { ChatInspector, ChatScreen } from "./screens/ChatScreen";
import { QueueScreen } from "./screens/QueueScreen";
import { ContactsScreen } from "./screens/ContactsScreen";
import { ProfilesScreen } from "./screens/ProfilesScreen";
import { HealthScreen } from "./screens/HealthScreen";
import { Dot, Toast } from "./components/ui";

type Tab = "console" | "chat" | "queue" | "contacts" | "profiles" | "health";

const TAB_LABEL: Record<Tab, string> = {
  console: "控制台",
  chat: "会话",
  queue: "上报队列",
  contacts: "通讯录",
  profiles: "设定集",
  health: "运行状态",
};

export default function App() {
  const [tab, setTab] = useState<Tab>("console");
  const [conversations, setConversations] = useState<Conversation[]>([]);
  const [threads, setThreads] = useState<Thread[]>([]);
  const [showArchived, setShowArchived] = useState(false);
  const [selectedId, setSelectedId] = useState("");
  const [selectedThreadId, setSelectedThreadId] = useState("");
  const [liveRun, setLiveRun] = useState<Run | null>(null);
  const [connected, setConnected] = useState(false);
  const [tick, setTick] = useState(0);
  const [toast, setToast] = useState<{ message: string; tone?: "info" | "danger" }>({
    message: "",
  });
  const toastTimer = useRef<number | null>(null);

  const notify = useCallback((message: string, tone?: "info" | "danger") => {
    setToast({ message, tone });
    if (toastTimer.current) window.clearTimeout(toastTimer.current);
    toastTimer.current = window.setTimeout(() => setToast({ message: "" }), 2600);
  }, []);

  const refreshConversations = useCallback(() => {
    listConversations()
      .then((items) => {
        // 只留平台会话(QQ): desktop/thread 是控制台对话,归"对话"那一栏管
        const platform = items.filter((item) => item.platform !== "desktop");
        setConversations(platform);
        setSelectedId((current) => {
          if (current && platform.some((item) => item.id === current)) return current;
          return platform[0]?.id || "";
        });
      })
      .catch((error) => notify(`加载会话失败：${error.message}`, "danger"));
  }, [notify]);

  const refreshThreads = useCallback(() => {
    listThreads(showArchived)
      .then((items) => {
        setThreads(items);
        setSelectedThreadId((current) => {
          if (current && items.some((item) => item.id === current)) return current;
          return items[0]?.id || "";
        });
      })
      .catch((error) => notify(`加载对话失败：${error.message}`, "danger"));
  }, [notify, showArchived]);

  useEffect(() => {
    refreshConversations();
    refreshThreads();
  }, [refreshConversations, refreshThreads]);

  // SSE：事件类型不同,处理方式不同
  //   step / run  → 控制台的执行轨迹(流式追加,不重拉)
  //   其它        → tick +1，各屏据此重拉自己关心的数据
  useEffect(() => {
    const unsubscribe = subscribe((event) => {
      if (event.type === "open") {
        setConnected(true);
        return;
      }
      if (event.type === "error") {
        setConnected(false);
        return;
      }
      if (event.type === "step") {
        const { run_id, step } = event.data || {};
        setLiveRun((current) => {
          if (!current || current.id !== run_id) return current;
          const exists = current.steps.some((item: any) => item.id === step.id);
          return exists
            ? current
            : { ...current, steps: [...current.steps, step] };
        });
        return;
      }
      if (event.type === "run") {
        const run: Run = (event.data || {}).run;
        setLiveRun((current) => (current && current.id === run?.id ? run : current));
        if (run && run.status !== "running") {
          // 收尾后排一次列表刷新(线程的"最近活跃"变了)
          setTick((value) => value + 1);
        }
        return;
      }
      setTick((value) => value + 1);
      if (event.type === "draft") notify("拟好了一条待确认草稿");
      if (event.type === "report") notify("有一条消息值得看一眼");
    });
    return unsubscribe;
  }, [notify]);

  // 列表不需要每来一条消息都重排，但空闲时刷一下能拿到"最近活跃"
  useEffect(() => {
    const timer = window.setInterval(() => {
      refreshConversations();
      refreshThreads();
    }, 30_000);
    return () => window.clearInterval(timer);
  }, [refreshConversations, refreshThreads]);

  const selected = useMemo(
    () => conversations.find((item) => item.id === selectedId) || null,
    [conversations, selectedId]
  );
  const selectedThread = useMemo(
    () => threads.find((item) => item.id === selectedThreadId) || null,
    [threads, selectedThreadId]
  );

  async function newThread() {
    try {
      const thread = await createThread("");
      setShowArchived(false);
      await refreshThreads();
      setSelectedThreadId(thread.id);
      setTab("console");
    } catch (error: any) {
      notify(`新建对话失败：${error.message}`, "danger");
    }
  }

  const inspectorOn = tab === "console" || tab === "chat";

  return (
    <div className="shell" data-inspector={inspectorOn ? "on" : "off"}>
      <div className="brand">
        <span className="mark" />
        <span className="name">Memo Echo</span>
        <span className="sub">值守控制台</span>
      </div>

      <div className="topbar">
        <div className="tabs">
          {(Object.keys(TAB_LABEL) as Tab[]).map((key) => (
            <button
              key={key}
              className="tab"
              data-active={tab === key}
              onClick={() => setTab(key)}
            >
              {TAB_LABEL[key]}
            </button>
          ))}
        </div>
        <span className="spacer" />
        <span className="row" style={{ gap: 6 }}>
          <Dot tone={connected ? "ok" : "warn"} />
          <span className="mono">{connected ? "实时已连接" : "实时断开"}</span>
        </span>
      </div>

      {tab === "console" ? (
        <ThreadSidebar
          threads={threads}
          selectedId={selectedThreadId}
          onSelect={setSelectedThreadId}
          onCreate={newThread}
          onToggleArchived={(next) => {
            setShowArchived(next);
          }}
          showArchived={showArchived}
          busy={Boolean(liveRun && liveRun.status === "running")}
        />
      ) : (
        <Sidebar
          conversations={conversations}
          selectedId={selectedId}
          onSelect={setSelectedId}
          previews={{}}
        />
      )}

      {tab === "console" ? (
        <>
          <ConsoleScreen
            thread={selectedThread}
            refreshTick={tick}
            notify={notify}
            onChanged={refreshThreads}
            liveRun={liveRun}
            onLiveRun={setLiveRun}
          />
          <ConsoleInspector
            thread={selectedThread}
            notify={notify}
            onChanged={refreshThreads}
          />
        </>
      ) : null}

      {tab === "chat" ? (
        <>
          <ChatScreen
            conversation={selected}
            refreshTick={tick}
            notify={notify}
            onConversationChanged={refreshConversations}
          />
          <ChatInspector
            conversation={selected}
            onChanged={refreshConversations}
            notify={notify}
          />
        </>
      ) : null}

      {tab === "queue" ? (
        <QueueScreen conversations={conversations} refreshTick={tick} notify={notify} />
      ) : null}

      {tab === "contacts" ? (
        <ContactsScreen notify={notify} onChanged={refreshConversations} />
      ) : null}

      {tab === "profiles" ? (
        <ProfilesScreen
          conversations={conversations}
          notify={notify}
          onChanged={refreshConversations}
        />
      ) : null}

      {tab === "health" ? <HealthScreen refreshTick={tick} notify={notify} /> : null}

      <div className="statusbar">
        <span>{conversations.length} 个值守会话</span>
        <span>{conversations.filter((item) => item.policy?.monitor).length} 个监视中</span>
        <span>{threads.length} 条对话</span>
        <span className="spacer" style={{ flex: 1 }} />
        <span>本机 · {window.location.host}</span>
      </div>

      <Toast message={toast.message} tone={toast.tone} />
    </div>
  );
}
