import { useCallback, useEffect, useState } from "react";
import {
  createThread,
  getThreadMessage,
  listThreadMessages,
  listThreads,
  sendThreadMessage,
  streamThreadMessage,
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
      .then((items) => {
        setThreads(items);
        // 首次加载时自动选中最近更新的非归档线程，刷新后可直接继续对话。
        setActiveThreadId((current) => {
          if (current && items.some((item) => item.id === current)) return current;
          const latest = items.find((item) => !item.archived) || items[0];
          return latest ? latest.id : null;
        });
      })
      .catch((reason) => setError(reason instanceof Error ? reason.message : "会话列表加载失败"))
      .finally(() => setThreadsLoading(false));
  }, [credential]);

  // 自动选中线程后加载其消息（仅在 activeThreadId 变化且来自自动选择时触发）。
  useEffect(() => {
    if (!activeThreadId) return;
    listThreadMessages(credential, activeThreadId)
      .then((items) => setMessages([...items].reverse()))
      .catch((reason) => setError(reason instanceof Error ? reason.message : "消息加载失败"));
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [activeThreadId]);

  function selectThread(threadId: string) {
    // 消息加载由 activeThreadId 变化触发，这里只切换选中与清空旧消息。
    setActiveThreadId(threadId);
    setError("");
    setMessages([]);
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
    const optimisticId = `local-user-${Date.now()}`;
    // 乐观追加用户消息与 pending agent 消息，POST 返回后用真实记录替换。
    const optimisticUser: ThreadMessage = {
      id: optimisticId,
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
    let agentMessageId = "";
    try {
      const result = await sendThreadMessage(credential, activeThreadId, content);
      agentMessageId = result.agentMessage.id;
      // 用真实 user 消息替换乐观占位，并挂上 streaming agent 消息。
      setMessages((current) => [
        ...current.filter((item) => item.id !== optimisticId),
        result.userMessage,
        { ...result.agentMessage, content: result.agentMessage.content || "正在处理…", liveTasks: [] },
      ]);
      // 订阅 SSE 阶段事件：progress 更新气泡文本与实时任务卡，done 替换为终态。
      const controller = new AbortController();
      try {
        await streamThreadMessage(credential, activeThreadId, agentMessageId, (payload) => {
          if (payload.stage === "progress") {
            const tasks = payload.tasks || [];
            const latestTask = tasks.slice(-1)[0];
            setMessages((current) =>
              current.map((item) =>
                item.id === agentMessageId && item.status === "streaming"
                  ? {
                      ...item,
                      content: latestTask?.progressSummary || "正在执行…",
                      liveTasks: tasks,
                    }
                  : item,
              ),
            );
          } else if (payload.stage === "done" && payload.agentMessage) {
            const { liveTasks: _droppedLiveTasks, ...finalMessage } = payload.agentMessage;
            setMessages((current) => [
              ...current.filter((item) => item.id !== agentMessageId),
              finalMessage,
            ]);
          } else if (payload.stage === "error") {
            setMessages((current) => [
              ...current.filter((item) => item.id !== agentMessageId),
              {
                id: agentMessageId,
                threadId: activeThreadId,
                role: "agent",
                content: payload.message || "执行失败",
                status: "error",
                executionId: null,
                taskId: null,
                workflowId: null,
                resultJson: null,
                createdAt: new Date().toISOString(),
                
              },
            ]);
          }
        }, controller.signal);
      } catch (reason) {
        // 流中断（如服务重启）：回退到服务端状态读取。
        try {
          const fresh = await getThreadMessage(credential, activeThreadId, agentMessageId);
          setMessages((current) => [
            ...current.filter((item) => item.id !== agentMessageId),
            { ...fresh, liveTasks: undefined },
          ]);
        } catch {
          // 保持 streaming 占位，由下次列表刷新兜底。
        }
      }
    } catch (reason) {
      setMessages((current) => [...current, {
        id: `local-error-${Date.now()}`,
        threadId: activeThreadId,
        role: "agent",
        content: reason instanceof Error ? reason.message : "命令执行失败",
        status: "error",
        executionId: null,
        taskId: null,
        workflowId: null,
        resultJson: null,
        createdAt: new Date().toISOString(),
        
      }]);
    } finally {
      setSending(false);
      refreshThreads();
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