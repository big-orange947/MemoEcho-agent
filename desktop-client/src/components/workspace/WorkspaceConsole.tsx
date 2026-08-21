import { useCallback, useEffect, useState } from "react";
import {
  createThread,
  listThreadMessages,
  listThreads,
  sendThreadMessage,
  updateThread,
} from "../../api/client";
import type { StoredCredential, Thread, ThreadMessage } from "../../types";
import { ChatView } from "./ChatView";
import { ThreadSidebar } from "./ThreadSidebar";

/** 主控台对话式工作区：左侧会话列表 + 中间聊天流。 */
export function WorkspaceConsole({ credential }: { credential: StoredCredential }) {
  const [threads, setThreads] = useState<Thread[]>([]);
  const [threadsLoading, setThreadsLoading] = useState(true);
  const [activeThreadId, setActiveThreadId] = useState<string | null>(null);
  const [messages, setMessages] = useState<ThreadMessage[]>([]);
  const [sending, setSending] = useState(false);
  const [error, setError] = useState("");

  const refreshThreads = useCallback(() => {
    setThreadsLoading(true);
    listThreads(credential, true)
      .then(setThreads)
      .catch((reason) => setError(reason instanceof Error ? reason.message : "会话列表加载失败"))
      .finally(() => setThreadsLoading(false));
  }, [credential]);

  useEffect(() => {
    refreshThreads();
  }, [refreshThreads]);

  function selectThread(threadId: string) {
    setActiveThreadId(threadId);
    setError("");
    setMessages([]);
    listThreadMessages(credential, threadId)
      .then((items) => setMessages([...items].reverse()))
      .catch((reason) => setError(reason instanceof Error ? reason.message : "消息加载失败"));
  }

  async function handleCreateThread() {
    try {
      const thread = await createThread(credential);
      setThreads((current) => [thread, ...current]);
      selectThread(thread.id);
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "新建对话失败");
    }
  }

  async function handleArchive(thread: Thread) {
    try {
      const updated = await updateThread(credential, thread.id, { archived: true });
      setThreads((current) => current.map((item) => (item.id === updated.id ? updated : item)));
      if (activeThreadId === thread.id) setActiveThreadId(null);
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "归档失败");
    }
  }

  async function handleRename(thread: Thread) {
    const title = window.prompt("对话标题", thread.title || "");
    if (title === null) return;
    try {
      const updated = await updateThread(credential, thread.id, { title: title.trim() || thread.title });
      setThreads((current) => current.map((item) => (item.id === updated.id ? updated : item)));
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "重命名失败");
    }
  }

  async function handleSend(content: string) {
    if (!activeThreadId || sending) return;
    setSending(true);
    setError("");
    // 乐观追加用户消息与 pending agent 消息，接口返回后用真实记录替换。
    const optimisticUser: ThreadMessage = {
      id: `local-user-${Date.now()}`,
      threadId: activeThreadId,
      role: "user",
      content,
      status: "done",
      executionId: "",
      taskId: null,
      workflowId: null,
      resultJson: null,
      createdAt: new Date().toISOString(),
    };
    setMessages((current) => [...current, optimisticUser]);
    try {
      const result = await sendThreadMessage(credential, activeThreadId, content);
      setMessages((current) => [...current, result.agentMessage]);
      // 命令可能创建了任务，刷新线程排序；并刷新任务消息所在时间线。
      refreshThreads();
    } catch (reason) {
      setMessages((current) => [
        ...current,
        {
          id: `local-error-${Date.now()}`,
          threadId: activeThreadId,
          role: "agent",
          content: reason instanceof Error ? reason.message : "命令执行失败",
          status: "error",
          executionId: "",
          taskId: null,
          workflowId: null,
          resultJson: null,
          createdAt: new Date().toISOString(),
        },
      ]);
    } finally {
      setSending(false);
    }
  }

  return (
    <div className="ws-layout">
      <ThreadSidebar
        threads={threads}
        activeThreadId={activeThreadId}
        loading={threadsLoading}
        onSelect={selectThread}
        onCreate={() => void handleCreateThread()}
        onArchive={(thread) => void handleArchive(thread)}
        onRename={(thread) => void handleRename(thread)}
      />
      <ChatView
        credential={credential}
        messages={messages}
        sending={sending}
        error={error}
        onSend={(content) => void handleSend(content)}
      />
    </div>
  );
}