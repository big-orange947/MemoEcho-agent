/* =============================================================================
 * HealthScreen.tsx - 运行状态（排障用的一屏）
 * -----------------------------------------------------------------------------
 * 这套系统里最危险的故障是**静默的**：
 *   · 攒批总结解析失败 → 业务上等于"这批没值得记的"，水位线照常推进；
 *   · 记忆检索恒为空 → 表现为"agent 不记得"，没有任何报错；
 *   · 上报复核没跑成 → 消息照常上报，只是没经过模型判断。
 * 所以这一屏专盯这些指标：解析失败次数、待处理积压、整理结果、库体积。
 * ========================================================================== */

import { useEffect, useState } from "react";
import {
  MemoryHealth,
  StorageStatus,
  eventStats,
  memoryHealth,
  reportStats,
  storageStatus,
} from "../lib/api";
import { STATUS_LABEL, relativeTime } from "../lib/format";
import { Badge, Empty, KV, Section } from "../components/ui";

export function HealthScreen({
  refreshTick,
  notify,
}: {
  refreshTick: number;
  notify: (message: string, tone?: "info" | "danger") => void;
}) {
  const [health, setHealth] = useState<MemoryHealth | null>(null);
  const [storage, setStorage] = useState<StorageStatus | null>(null);
  const [reports, setReports] = useState<Record<string, number>>({});
  const [events, setEvents] = useState<Record<string, number>>({});

  useEffect(() => {
    let alive = true;
    Promise.all([
      memoryHealth().catch(() => null),
      storageStatus().catch(() => null),
      reportStats().catch(() => ({})),
      eventStats().catch(() => ({})),
    ]).then(([healthResult, storageResult, reportResult, eventResult]) => {
      if (!alive) return;
      setHealth(healthResult);
      setStorage(storageResult);
      setReports(reportResult || {});
      setEvents(eventResult || {});
    });
    return () => {
      alive = false;
    };
  }, [refreshTick, notify]);

  const summary = health?.summary || {};
  const unparsed = Number(summary.unparsed || 0);
  const pendingTotal = (reports.pending || 0) + (reports.claimed || 0);
  const dead = reports.dead || 0;

  return (
    <div className="main">
      <div className="scroll">
        <Section
          title="记忆"
          actions={
            health?.enabled ? (
              <Badge tone={health.init_error ? "danger" : "ok"}>
                {health.init_error ? "降级" : "已启用"}
              </Badge>
            ) : (
              <Badge tone="warn">未启用</Badge>
            )
          }
        >
          {health?.init_error ? (
            <div className="hint" style={{ marginBottom: 10 }}>
              初始化失败：{health.init_error}（记忆是增强能力，主链路不受影响）
            </div>
          ) : null}
          <KV
            items={[
              ["总结器调用", summary.calls ?? 0],
              ["判定无价值", summary.empty ?? 0],
              [
                "解析失败（可疑）",
                <span key="unparsed" style={{ color: unparsed ? "var(--danger)" : undefined }}>
                  {unparsed}
                  {summary.unparsed_ratio ? `（${Math.round(summary.unparsed_ratio * 100)}%）` : ""}
                </span>,
              ],
            ]}
          />
          {unparsed ? (
            <div className="hint" style={{ marginTop: 8, color: "var(--danger)" }}>
              解析失败意味着那批消息**再也不会被总结**（水位线照常推进）。样本：
              <div className="mono" style={{ marginTop: 4, whiteSpace: "pre-wrap" }}>
                {String(summary.last_unparsed_sample || "").slice(0, 200)}
              </div>
            </div>
          ) : null}
        </Section>

        <Section title="攒批进度">
          {!health?.batches?.length ? (
            <div className="hint">还没有会话进入攒批流程。</div>
          ) : (
            <div className="stack" style={{ gap: 8 }}>
              {health.batches.slice(0, 12).map((batch) => (
                <div key={String(batch.conversation_id)} className="row between">
                  <span className="mono">{String(batch.conversation_id).slice(0, 10)}</span>
                  <span className="row" style={{ gap: 6 }}>
                    <span className="chip">待处理 {batch.pending_count ?? 0}</span>
                    <Badge
                      tone={
                        batch.last_status === "error"
                          ? "danger"
                          : batch.last_status === "ok"
                            ? "ok"
                            : undefined
                      }
                    >
                      {batch.last_status || "未跑"}
                    </Badge>
                    <span className="mono">{relativeTime(String(batch.last_run_at || ""))}</span>
                  </span>
                </div>
              ))}
            </div>
          )}
        </Section>

        <Section title="记忆整理（过期 / 冲突）">
          {!health?.consolidation?.length ? (
            <div className="hint">还没有会话跑过整理。</div>
          ) : (
            <div className="stack" style={{ gap: 8 }}>
              {health.consolidation.slice(0, 12).map((item) => {
                const operations = (item.operations || {}) as Record<string, number>;
                return (
                  <div key={String(item.conversation_id)} className="row between">
                    <span className="mono">{String(item.conversation_id).slice(0, 10)}</span>
                    <span className="row" style={{ gap: 6 }}>
                      {Object.entries(operations).map(([name, count]) => (
                        <span key={name} className="chip">
                          {name} {count}
                        </span>
                      ))}
                      {item.conflicts ? <Badge tone="question">冲突 {item.conflicts}</Badge> : null}
                      <span className="mono">{relativeTime(String(item.last_run_at || ""))}</span>
                    </span>
                  </div>
                );
              })}
            </div>
          )}
        </Section>

        <Section
          title="上报队列"
          actions={dead ? <Badge tone="danger">死信 {dead}</Badge> : null}
        >
          <KV
            items={[
              ["待消费", pendingTotal],
              ["待复核", reports.candidate || 0],
              ["已处理", reports.acked || 0],
              ["已丢弃", reports.dropped || 0],
              ["死信", dead],
            ]}
          />
          {dead ? (
            <div className="hint" style={{ marginTop: 8, color: "var(--danger)" }}>
              死信是认领超过 5 次仍未处理完的记录，通常意味着消费方一直失败。
            </div>
          ) : null}
        </Section>

        <Section title="存储">
          {!storage ? (
            <Empty>读不到存储状态。</Empty>
          ) : (
            <div className="stack">
              <KV
                items={[
                  ["合计", storage.total_human],
                  ...Object.entries(storage.files || {}).map(
                    ([name, info]) => [name, info.human] as [string, string]
                  ),
                ]}
              />
              <div className="row" style={{ flexWrap: "wrap", gap: 6 }}>
                {Object.entries(storage.rows || {}).map(([table, count]) => (
                  <span key={table} className="chip">
                    {table} {count}
                  </span>
                ))}
              </div>
              <div className="hint">
                保留策略由调度器每 24 小时执行一次；消息只在**已总结进长期记忆**之后才可能被删。
              </div>
            </div>
          )}
        </Section>

        <Section title="事件审计">
          {Object.keys(events).length === 0 ? (
            <div className="hint">暂无事件统计。</div>
          ) : (
            <div className="row" style={{ flexWrap: "wrap", gap: 6 }}>
              {Object.entries(events).map(([key, value]) => (
                <span key={key} className="chip">
                  {STATUS_LABEL[key] || key} {value}
                </span>
              ))}
            </div>
          )}
        </Section>
      </div>
    </div>
  );
}
