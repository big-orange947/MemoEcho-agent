/* =============================================================================
 * App.tsx - 应用外壳
 * -----------------------------------------------------------------------------
 * 布局：左侧会话列表 / 中间主区（四个标签页）/ 右侧检查器 / 底部状态栏。
 *
 * 数据流刻意保持"笨"：
 *   一个 refreshTick 计数器 + 各屏自己拉数据。SSE 事件到达就把 tick +1，
 *   谁需要刷新谁重拉。没有引入状态管理库 —— 这个体量下它只会增加间接层，
 *   真正麻烦的是"哪些数据该在什么时候失效"，那件事用 tick 反而看得更清楚。
 * ========================================================================== */

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { Conversation, listConversations } from "./lib/api";
import { subscribe } from "./lib/sse";
import { Sidebar } from "./components/Sidebar";
import { ConsoleInspector, ConsoleScreen } from "./screens/ConsoleScreen";
import { QueueScreen } from "./screens/QueueScreen";
import { ContactsScreen } from "./screens/ContactsScreen";
import { ProfilesScreen } from "./screens/ProfilesScreen";
import { HealthScreen } from "./screens/HealthScreen";
import { Dot, Toast } from "./components/ui";

type Tab = "console" | "queue" | "contacts" | "profiles" | "health";

const TAB_LABEL: Record<Tab, string> = {
  console: "控制台",
  queue: "上报队列",
  contacts: "通讯录",
  profiles: "设定集",
  health: "运行状态",
};

export default function App() {
  const [tab, setTab] = useState<Tab>("console");
  const [conversations, setConversations] = useState<Conversation[]>([]);
  const [selectedId, setSelectedId] = useState("");
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
        setConversations(items);
        setSelectedId((current) => {
          if (current && items.some((item) => item.id === current)) return current;
          return items[0]?.id || "";
        });
      })
      .catch((error) => notify(`加载会话失败：${error.message}`, "danger"));
  }, [notify]);

  useEffect(() => {
    refreshConversations();
  }, [refreshConversations]);

  // SSE：收到任何事件就 +1，各屏据此重拉自己关心的数据
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
      setTick((value) => value + 1);
      if (event.type === "draft") {
        notify("拟好了一条待确认草稿");
      }
      if (event.type === "report") {
        notify("有一条消息值得看一眼");
      }
    });
    return unsubscribe;
  }, [notify]);

  // 会话列表不需要每来一条消息都重排，但空闲时刷一下能拿到 last_message_at
  useEffect(() => {
    const timer = window.setInterval(refreshConversations, 30_000);
    return () => window.clearInterval(timer);
  }, [refreshConversations]);

  const selected = useMemo(
    () => conversations.find((item) => item.id === selectedId) || null,
    [conversations, selectedId]
  );

  return (
    <div className="shell" data-inspector={tab === "console" ? "on" : "off"}>
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

      <Sidebar
        conversations={conversations}
        selectedId={selectedId}
        onSelect={(id) => {
          setSelectedId(id);
          setTab("console");
        }}
        previews={{}}
      />

      {tab === "console" ? (
        <>
          <ConsoleScreen
            conversation={selected}
            refreshTick={tick}
            notify={notify}
            onConversationChanged={refreshConversations}
          />
          <ConsoleInspector
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

      {tab === "health" ? (
        <>
          <HealthScreen refreshTick={tick} notify={notify} />

        </>
      ) : null}

      <div className="statusbar">
        <span>{conversations.length} 个会话</span>
        <span>
          {conversations.filter((item) => item.policy?.monitor).length} 个监视中
        </span>
        <span>
          {conversations.filter((item) => item.policy?.alert_enabled).length} 个开了上报
        </span>
        <span className="spacer" style={{ flex: 1 }} />
        <span>本机 · {window.location.host}</span>
      </div>

      <Toast message={toast.message} tone={toast.tone} />
    </div>
  );
}
