/* =============================================================================
 * Sidebar.tsx - 左侧列表(两种形态)
 * -----------------------------------------------------------------------------
 * 控制台标签页 → 列"对话"(与 agent 的线程): 那是你下指令的地方;
 * 其余标签页   → 列"值守会话"(QQ 好友/群): 那是 agent 替你盯着的地方。
 *
 * 为什么要分: 两类东西混在一起时,"点一条看看"会分不清点开的是
 * "我和 agent 的对话"还是"我在偷看某个好友的聊天"。
 *
 * 会话列表一眼能看出"哪些会话在被管、怎么管": 监视/自动回/草稿/上报 四个旗标
 * 直接标在行上,否则每次都要点进去才知道策略。
 * ========================================================================== */

import type { Conversation, Thread } from "../lib/api";
import { conversationLabel, policyFlags, relativeTime } from "../lib/format";
import { Button, Dot, Empty } from "./ui";

/* ------------------------------------------------------------------ 对话线程 */
export function ThreadSidebar({
  threads,
  selectedId,
  onSelect,
  onCreate,
  onToggleArchived,
  showArchived,
  busy,
}: {
  threads: Thread[];
  selectedId: string;
  onSelect: (id: string) => void;
  onCreate: () => void;
  onToggleArchived: (show: boolean) => void;
  showArchived: boolean;
  busy: boolean;
}) {
  return (
    <aside className="sidebar">
      <div className="section" style={{ paddingBottom: 10 }}>
        <h3 className="section-title">
          对话
          <span className="spacer" />
          <span className="mono">{threads.length}</span>
        </h3>
        <Button size="sm" onClick={onCreate} disabled={busy} title="开一条新对话">
          + 新建对话
        </Button>
      </div>
      <div className="scroll">
        {threads.length === 0 ? (
          <Empty>
            还没有对话。
            <br />
            点"新建对话",然后直接说你想要什么 ——
            例如"帮我问一下 km 今晚有没有空打游戏"。
          </Empty>
        ) : (
          threads.map((thread) => (
            <button
              key={thread.id}
              type="button"
              className="conv"
              data-active={thread.id === selectedId}
              onClick={() => onSelect(thread.id)}
            >
              <div className="line1">
                <Dot tone={thread.running ? "accent" : undefined} title={thread.running ? "正在执行" : ""} />
                <span className="who">{thread.title || "新对话"}</span>
                <span className="time">{relativeTime(thread.updated_at)}</span>
              </div>
              <div className="line2">
                <span className="preview">
                  {thread.running ? `执行中（${thread.running}）` : thread.archived ? "已归档" : ""}
                </span>
              </div>
            </button>
          ))
        )}
      </div>
      <div className="section" style={{ paddingTop: 8, paddingBottom: 8 }}>
        <label className="switch" title="归档是收进抽屉,不是删除">
          <input
            type="checkbox"
            checked={showArchived}
            onChange={(event) => onToggleArchived(event.target.checked)}
          />
          <span>显示已归档</span>
        </label>
      </div>
    </aside>
  );
}

/* ------------------------------------------------------------------ 值守会话 */
export function Sidebar({
  conversations,
  selectedId,
  onSelect,
  previews,
}: {
  conversations: Conversation[];
  selectedId: string;
  onSelect: (id: string) => void;
  previews: Record<string, string>;
}) {
  return (
    <aside className="sidebar">
      <div className="section" style={{ paddingBottom: 10 }}>
        <h3 className="section-title">
          值守会话
          <span className="spacer" />
          <span className="mono">{conversations.length}</span>
        </h3>
      </div>
      <div className="scroll">
        {conversations.length === 0 ? (
          <Empty>
            还没有值守会话。
            <br />
            去"通讯录"勾选要盯的好友/群,一键建出来。
          </Empty>
        ) : (
          conversations.map((conversation) => {
            const flags = policyFlags(conversation.policy);
            const preview = previews[conversation.id] || "";
            return (
              <button
                key={conversation.id}
                type="button"
                className="conv"
                data-active={conversation.id === selectedId}
                onClick={() => onSelect(conversation.id)}
              >
                <div className="line1">
                  <Dot
                    tone={
                      conversation.policy?.alert_enabled
                        ? "accent"
                        : conversation.policy?.monitor
                          ? "ok"
                          : undefined
                    }
                    title={conversation.policy?.monitor ? "监视中" : "未监视"}
                  />
                  <span className="who">{conversationLabel(conversation)}</span>
                  <span className="time">
                    {relativeTime(
                      conversation.last_message_at || conversation.updated_at || ""
                    )}
                  </span>
                </div>
                <div className="line2">
                  <span className="preview">
                    {preview || conversation.persona || conversation.platform}
                  </span>
                  <span className="spacer" />
                  <span className="flags">
                    {flags.map((flag) => (
                      <span key={flag.kind} className="flag" data-on="true" data-kind={flag.kind}>
                        {flag.label}
                      </span>
                    ))}
                  </span>
                </div>
              </button>
            );
          })
        )}
      </div>
    </aside>
  );
}
