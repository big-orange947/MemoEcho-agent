import { Plus, PencilSimple, Archive, Check } from "@phosphor-icons/react";
import type { Thread } from "../../types";

/** 左侧工作区侧栏：新建对话、线程列表与归档区。 */
export function ThreadSidebar({
  threads,
  activeThreadId,
  loading,
  onSelect,
  onCreate,
  onArchive,
  onRename,
}: {
  threads: Thread[];
  activeThreadId: string | null;
  loading: boolean;
  onSelect: (threadId: string) => void;
  onCreate: () => void;
  onArchive: (thread: Thread) => void;
  onRename: (thread: Thread) => void;
}) {
  const active = threads.filter((thread) => !thread.archived);
  const archived = threads.filter((thread) => thread.archived);

  return (
    <div className="ws-sidebar">
      <div className="ws-sidebar-head">
        <strong>对话</strong>
        <button type="button" className="ws-new-thread" onClick={onCreate} aria-label="新建对话">
          <Plus size={16} weight="bold" />
        </button>
      </div>
      {loading && <p className="ws-sidebar-notice">正在恢复对话…</p>}
      <div className="ws-thread-list">
        {active.map((thread) => (
          <button
            key={thread.id}
            type="button"
            className={`ws-thread-item ${thread.id === activeThreadId ? "active" : ""}`}
            onClick={() => onSelect(thread.id)}
          >
            <span className="ws-thread-title">{thread.title || "未命名对话"}</span>
            <span className="ws-thread-actions">
              <i
                role="button"
                tabIndex={0}
                aria-label="重命名"
                onClick={(event) => {
                  event.stopPropagation();
                  onRename(thread);
                }}
              >
                <PencilSimple size={13} />
              </i>
              <i
                role="button"
                tabIndex={0}
                aria-label="归档"
                onClick={(event) => {
                  event.stopPropagation();
                  onArchive(thread);
                }}
              >
                <Archive size={13} />
              </i>
            </span>
          </button>
        ))}
        {!loading && active.length === 0 && <p className="ws-sidebar-notice">还没有对话，点击 + 新建</p>}
      </div>
      {archived.length > 0 && (
        <div className="ws-thread-archived">
          <p>已归档</p>
          {archived.map((thread) => (
            <button
              key={thread.id}
              type="button"
              className="ws-thread-item archived"
              onClick={() => onSelect(thread.id)}
              title="点击恢复查看，再点一次取消归档待扩展"
            >
              <Check size={13} />
              <span className="ws-thread-title">{thread.title || "未命名对话"}</span>
            </button>
          ))}
        </div>
      )}
    </div>
  );
}