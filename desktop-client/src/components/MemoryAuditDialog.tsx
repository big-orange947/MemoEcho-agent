import { useEffect } from "react";
import { ArrowClockwise, Brain, ChatCircleDots, X } from "@phosphor-icons/react";

import type { MemoryCandidate } from "../types";

type MemoryAuditDialogProps = {
  open: boolean;
  eventId: string;
  memoryIds: string[];
  memories: MemoryCandidate[];
  loading: boolean;
  error: string;
  onClose: () => void;
  onRetry: () => void;
  onViewEvidence: (memory: MemoryCandidate) => void;
};

/**
 * 展示一次 Agent 执行实际读取的长期记忆，而不是展示当前账户的全部记忆。
 * 这里只按服务端执行轨迹中的 ID 建立关联，避免前端根据文本相似度猜测“可能使用过”的内容。
 */
export function MemoryAuditDialog({
  open,
  eventId,
  memoryIds,
  memories,
  loading,
  error,
  onClose,
  onRetry,
  onViewEvidence,
}: MemoryAuditDialogProps) {
  /** 支持 Esc 关闭审计弹窗，并在卸载时移除键盘监听。 */
  useEffect(() => {
    if (!open) return undefined;
    const handleKeyDown = (event: KeyboardEvent) => {
      if (event.key === "Escape") onClose();
    };
    window.addEventListener("keydown", handleKeyDown);
    return () => window.removeEventListener("keydown", handleKeyDown);
  }, [open, onClose]);

  if (!open) return null;

  const memoryById = new Map(memories.map((memory) => [memory.id, memory]));
  const resolvedCount = memoryIds.filter((id) => memoryById.has(id)).length;

  return (
    <div className="context-dialog-backdrop" onMouseDown={onClose}>
      <section
        className="memory-audit-dialog"
        role="dialog"
        aria-modal="true"
        aria-label="本次执行使用的长期记忆"
        onMouseDown={(event) => event.stopPropagation()}
      >
        <header className="memory-audit-header">
          <div>
            <span><Brain size={20} weight="duotone" /></span>
            <div><b>本次使用的长期记忆</b><small>EXECUTION MEMORY AUDIT</small></div>
          </div>
          <button type="button" onClick={onClose} aria-label="关闭记忆审计"><X size={18} /></button>
        </header>

        {loading ? (
          <div className="memory-audit-state" aria-live="polite">
            <Brain size={27} />
            <b>正在读取执行依据</b>
            <p>正在核对执行轨迹与当前账户可见的长期记忆</p>
          </div>
        ) : error ? (
          <div className="memory-audit-state error" role="alert">
            <Brain size={27} />
            <b>执行依据读取失败</b>
            <p>{error}</p>
            <button type="button" onClick={onRetry}><ArrowClockwise size={16} />重新读取</button>
          </div>
        ) : (
          <div className="memory-audit-content">
            <div className="memory-audit-summary">
              <div><small>执行事件</small><code>{eventId}</code></div>
              <p>此次执行读取了 <b>{memoryIds.length}</b> 条已确认记忆，当前可回查 <b>{resolvedCount}</b> 条。</p>
            </div>
            {memoryIds.length === 0 ? (
              <div className="memory-audit-empty">
                <ChatCircleDots size={25} />
                <b>本次没有使用长期记忆</b>
                <span>回复可能仅使用了当前上下文、会话设定、Skill 或外部知识。</span>
              </div>
            ) : (
              <div className="memory-audit-list">
                {memoryIds.map((id, index) => {
                  const memory = memoryById.get(id);
                  return memory ? (
                    <article key={id}>
                      <i>{String(index + 1).padStart(2, "0")}</i>
                      <div>
                        <header><b>{memory.subject} · {memory.predicate}</b><span>{memory.status}</span></header>
                        <p>{memory.value}</p>
                        <small>{formatMemoryScope(memory)} · 置信度 {Math.round(memory.confidence * 100)}%</small>
                      </div>
                      <button type="button" onClick={() => onViewEvidence(memory)}>查看来源</button>
                    </article>
                  ) : (
                    <article className="missing" key={id}>
                      <i>{String(index + 1).padStart(2, "0")}</i>
                      <div><header><b>记录当前不可见</b></header><p>该记忆可能已删除、过期或不属于当前账户。</p><code>{id}</code></div>
                    </article>
                  );
                })}
              </div>
            )}
          </div>
        )}
      </section>
    </div>
  );
}

/** 把后端作用域转换为用户可理解的简短标签，不在界面泄露空占位字段。 */
function formatMemoryScope(memory: MemoryCandidate) {
  if (memory.scopeType === "CONVERSATION") return `${memory.platform || "平台"} · 当前会话`;
  if (memory.scopeType === "SCENE") return `场景 · ${memory.scene || "未命名"}`;
  if (memory.scopeType === "PLATFORM") return `平台 · ${memory.platform || "未命名"}`;
  return "全局记忆";
}
