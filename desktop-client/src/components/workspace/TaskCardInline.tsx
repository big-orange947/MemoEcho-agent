import { useEffect, useState } from "react";
import { CircleNotch, CaretDown, CheckCircle } from "@phosphor-icons/react";
import { getDelegatedTask } from "../../api/client";
import type { DelegatedTask, StoredCredential, ThreadLiveTask, ThreadMessage } from "../../types";

const TERMINAL_STATUSES = new Set(["COMPLETED", "FAILED", "CANCELLED"]);

function taskStatusLabel(status: string) {
  const normalized = String(status || "").toUpperCase();
  if (normalized === "COMPLETED") return "已完成";
  if (normalized === "ACTIVE" || normalized === "RUNNING") return "进行中";
  if (normalized === "WAITING_TARGET") return "等待目标";
  if (normalized === "PAUSED") return "已暂停";
  if (normalized === "FAILED") return "失败";
  if (normalized === "CANCELLED") return "已取消";
  return status || "未知";
}

/** 流式期间的实时任务卡：直接渲染 SSE progress 事件携带的任务视图。 */
export function LiveTaskCards({ tasks }: { tasks: ThreadLiveTask[] }) {
  if (!tasks || tasks.length === 0) return null;
  return (
    <div className="ws-live-tasks">
      {tasks.map((task) => {
        const terminal = TERMINAL_STATUSES.has(String(task.status).toUpperCase());
        return (
          <div key={task.id} className={`ws-task-card ${terminal ? "ws-task-card-done" : ""}`}>
            <div className="ws-task-card-head">
              <span className={`ws-task-state state-${String(task.status).toLowerCase()}`}>
                {taskStatusLabel(task.status)}
              </span>
              <strong>{task.objective || task.stepKey || "委托任务"}</strong>
              {terminal && <CheckCircle size={14} />}
            </div>
            {task.progressSummary && (
              <div className="ws-task-card-body">
                <p>{task.progressSummary}</p>
              </div>
            )}
          </div>
        );
      })}
    </div>
  );
}

/** 内嵌在 agent 消息下的紧凑委托任务卡片，展示执行状态与进度摘要。 */
export function TaskCardInline({
  credential,
  message,
}: {
  credential: StoredCredential;
  message: ThreadMessage;
}) {
  const [task, setTask] = useState<DelegatedTask | null | undefined>(undefined);
  const [expanded, setExpanded] = useState(false);

  useEffect(() => {
    const taskId = message.taskId;
    if (!taskId) return;
    let cancelled = false;
    void getDelegatedTask(credential, taskId)
      .then((value) => {
        if (!cancelled) setTask(value);
      })
      .catch(() => {
        if (!cancelled) setTask(null);
      });
    return () => {
      cancelled = true;
    };
  }, [credential, message.taskId]);

  if (task === undefined) {
    return (
      <div className="ws-task-card ws-task-card-loading">
        <CircleNotch className="spinning" size={15} />读取任务状态…
      </div>
    );
  }
  if (task === null) {
    return <div className="ws-task-card">委托任务已不可见（可能已清理）</div>;
  }

  const terminal = TERMINAL_STATUSES.has(String(task.status).toUpperCase());
  return (
    <div className={`ws-task-card ${terminal ? "ws-task-card-done" : ""}`}>
      <div className="ws-task-card-head" onClick={() => setExpanded((current) => !current)}>
        <span className={`ws-task-state state-${String(task.status).toLowerCase()}`}>
          {taskStatusLabel(task.status)}
        </span>
        <strong>{task.objective || task.originalCommand || "委托任务"}</strong>
        <CaretDown className={expanded ? "ws-task-caret open" : "ws-task-caret"} size={14} />
      </div>
      {expanded && (
        <div className="ws-task-card-body">
          <p>{task.progressSummary || task.completionReport || "暂无进度摘要"}</p>
          <small>
            目标：{task.targetName || task.targetQuery || "—"}
            {terminal && <CheckCircle size={13} />}
          </small>
        </div>
      )}
    </div>
  );
}