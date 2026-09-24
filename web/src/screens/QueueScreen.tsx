/* =============================================================================
 * QueueScreen.tsx - 上报队列（这套系统最需要"人"的一屏）
 * -----------------------------------------------------------------------------
 * 队列里有五类东西，处理方式完全不同：
 *   urgent/normal/digest  看一眼就行 → 已处理 / 丢弃
 *   question（请示）        在等号主拍板 → 已处理（表示答复已回给 agent）
 *   draft（待确认草稿）      agent 拟好但**没发出去**的话 → 改一改再发 / 丢弃
 *
 * 所以草稿卡单独做了可编辑的输入框：草稿本来就是给人改的。
 * 发送失败时后端不会标记完成，卡片会留在原地，可以重试。
 * ========================================================================== */

import { useEffect, useMemo, useState } from "react";
import {
  Conversation,
  ReportRecord,
  ackReport,
  dropReport,
  listReports,
  sendDraft,
} from "../lib/api";
import { LANE_LABEL, LANE_TONE, STATUS_LABEL, conversationLabel, relativeTime } from "../lib/format";
import { Badge, Button, Empty } from "../components/ui";

const ACTIVE_STATUSES = ["candidate", "pending", "claimed"];
const LANE_ORDER = ["urgent", "question", "draft", "normal", "digest"];

export function QueueScreen({
  conversations,
  refreshTick,
  notify,
}: {
  conversations: Conversation[];
  refreshTick: number;
  notify: (message: string, tone?: "info" | "danger") => void;
}) {
  const [records, setRecords] = useState<ReportRecord[]>([]);
  const [lane, setLane] = useState<string>("");
  const [showHandled, setShowHandled] = useState(false);
  const [busyId, setBusyId] = useState("");
  const [edits, setEdits] = useState<Record<string, string>>({});

  useEffect(() => {
    let alive = true;
    listReports({ lane, limit: 200 })
      .then((items) => {
        if (alive) setRecords(items);
      })
      .catch((error) => notify(`加载队列失败：${error.message}`, "danger"));
    return () => {
      alive = false;
    };
  }, [lane, refreshTick, notify]);

  const conversationById = useMemo(() => {
    const map: Record<string, Conversation> = {};
    conversations.forEach((item) => (map[item.id] = item));
    return map;
  }, [conversations]);

  const visible = useMemo(() => {
    const filtered = showHandled
      ? records
      : records.filter((record) => ACTIVE_STATUSES.includes(String(record.status)));
    return [...filtered].sort((left, right) => {
      const laneDelta =
        LANE_ORDER.indexOf(String(left.lane)) - LANE_ORDER.indexOf(String(right.lane));
      if (laneDelta !== 0) return laneDelta;
      return String(right.created_at).localeCompare(String(left.created_at));
    });
  }, [records, showHandled]);

  const counts = useMemo(() => {
    const map: Record<string, number> = {};
    records
      .filter((record) => ACTIVE_STATUSES.includes(String(record.status)))
      .forEach((record) => {
        map[String(record.lane)] = (map[String(record.lane)] || 0) + 1;
      });
    return map;
  }, [records]);

  async function act(record: ReportRecord, action: "ack" | "drop" | "send") {
    setBusyId(record.id);
    try {
      if (action === "send") {
        const text = edits[record.id];
        await sendDraft(record.id, text && text.trim() ? text.trim() : undefined);
        notify("已发出");
      } else if (action === "ack") {
        await ackReport(record.id);
        notify("已标记处理");
      } else {
        await dropReport(record.id, "控制台丢弃");
        notify("已丢弃");
      }
      const items = await listReports({ lane, limit: 200 });
      setRecords(items);
    } catch (error: any) {
      notify(
        action === "send" ? `发送失败（记录仍保留，可重试）：${error.message}` : `操作失败：${error.message}`,
        "danger"
      );
    } finally {
      setBusyId("");
    }
  }

  return (
    <div className="main">
      <div className="section" style={{ paddingBottom: 12 }}>
        <div className="row" style={{ flexWrap: "wrap", gap: 6 }}>
          <div className="tabs">
            <button className="tab" data-active={lane === ""} onClick={() => setLane("")}>
              全部
            </button>
            {LANE_ORDER.map((value) => (
              <button
                key={value}
                className="tab"
                data-active={lane === value}
                onClick={() => setLane(value)}
              >
                {LANE_LABEL[value]}
                {counts[value] ? <span className="mono"> {counts[value]}</span> : null}
              </button>
            ))}
          </div>
          <span className="spacer" />
          <Button
            variant="ghost"
            size="sm"
            onClick={() => setShowHandled((value) => !value)}
            title="包含已处理/已丢弃/死信"
          >
            {showHandled ? "只看待处理" : "显示已处理"}
          </Button>
        </div>
      </div>

      <div className="scroll">
        {visible.length === 0 ? (
          <Empty>
            队列是空的。
            <br />
            只有开了"重要消息上报"的会话，命中规则时才会往这里放东西。
          </Empty>
        ) : (
          <div className="queue">
            {visible.map((record) => (
              <ReportCard
                key={record.id}
                record={record}
                conversation={conversationById[record.conversation_id]}
                busy={busyId === record.id}
                edit={edits[record.id]}
                onEdit={(text) => setEdits((prev) => ({ ...prev, [record.id]: text }))}
                onAct={(action) => act(record, action)}
              />
            ))}
          </div>
        )}
      </div>
    </div>
  );
}

function ReportCard({
  record,
  conversation,
  busy,
  edit,
  onEdit,
  onAct,
}: {
  record: ReportRecord;
  conversation?: Conversation;
  busy: boolean;
  edit?: string;
  onEdit: (text: string) => void;
  onAct: (action: "ack" | "drop" | "send") => void;
}) {
  const payload = record.payload || {};
  const lane = String(record.lane);
  const isDraft = lane === "draft";
  const isQuestion = lane === "question";
  const handled = !ACTIVE_STATUSES.includes(String(record.status));
  const draftText = edit ?? String(payload.text || payload.summary || "");
  const reasons: string[] = Array.isArray(payload.reasons) ? payload.reasons : [];

  return (
    <div className="card" data-lane={lane} style={handled ? { opacity: 0.55 } : undefined}>
      <div className="head">
        <Badge tone={LANE_TONE[lane]}>{LANE_LABEL[lane] || lane}</Badge>
        <span className="subject">
          {conversation ? conversationLabel(conversation) : record.conversation_id.slice(0, 8)}
        </span>
        {payload.sender_name ? <span className="chip">{payload.sender_name}</span> : null}
        {payload.event ? <span className="chip">{payload.event}</span> : null}
        {payload.score ? <span className="chip">{Number(payload.score).toFixed(2)}</span> : null}
        <span className="spacer" />
        {handled ? <Badge>{STATUS_LABEL[String(record.status)] || record.status}</Badge> : null}
        {!handled && record.status === "candidate" ? <Badge tone="warn">待复核</Badge> : null}
        <span className="mono">{relativeTime(record.created_at)}</span>
      </div>

      {payload.summary && !(isDraft && String(payload.summary) === String(payload.text)) ? (
        <div className="summary">{String(payload.summary)}</div>
      ) : null}
      {payload.text && !isDraft ? (
        <div className="original">原话：{String(payload.text)}</div>
      ) : null}
      {payload.options ? <div className="original">选项：{String(payload.options)}</div> : null}
      {payload.detail ? <div className="original">{String(payload.detail)}</div> : null}
      {payload.escalated ? <div className="chip">时间临近，已升级为急事</div> : null}
      {payload.stale ? <div className="chip">时间已过</div> : null}
      {payload.degraded ? <div className="chip">复核未执行（按规则上报）</div> : null}

      {reasons.length ? (
        <div className="reasons">
          {reasons.slice(0, 8).map((reason) => (
            <span key={reason} className="chip">
              {reason}
            </span>
          ))}
        </div>
      ) : null}

      {isDraft && !handled ? (
        <textarea
          className="textarea"
          value={draftText}
          onChange={(event) => onEdit(event.target.value)}
          placeholder="草稿内容（可以改一改再发）"
        />
      ) : null}

      {!handled ? (
        <div className="actions">
          {isDraft ? (
            <>
              <Button variant="primary" size="sm" disabled={busy} onClick={() => onAct("send")}>
                {busy ? "发送中…" : "发出"}
              </Button>
              <Button variant="danger" size="sm" disabled={busy} onClick={() => onAct("drop")}>
                丢弃
              </Button>
            </>
          ) : (
            <>
              <Button variant="primary" size="sm" disabled={busy} onClick={() => onAct("ack")}>
                {isQuestion ? "已答复" : "已处理"}
              </Button>
              <Button variant="danger" size="sm" disabled={busy} onClick={() => onAct("drop")}>
                不用报
              </Button>
            </>
          )}
          <span className="spacer" />
          <span className="mono">{record.id.slice(0, 8)}</span>
        </div>
      ) : null}
    </div>
  );
}
