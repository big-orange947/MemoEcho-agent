/* =============================================================================
 * Sidebar.tsx - 左侧会话列表
 * -----------------------------------------------------------------------------
 * 列表要能一眼看出"哪些会话在被管、怎么管"：
 *   监视 / 自动回 / 草稿 / 上报 四个旗标直接标在行上，
 *   否则每次都要点进去才知道策略。
 * ========================================================================== */

import type { Conversation } from "../lib/api";
import { conversationLabel, policyFlags, relativeTime } from "../lib/format";
import { Dot, Empty } from "./ui";

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
          会话
          <span className="spacer" />
          <span className="mono">{conversations.length}</span>
        </h3>
      </div>
      <div className="scroll">
        {conversations.length === 0 ? (
          <Empty>
            还没有会话。
            <br />
            对方发来第一条消息、或从主控台派个任务，这里就会出现。
          </Empty>
        ) : (
          conversations.map((conversation) => {
            const flags = policyFlags(conversation.policy);
            const preview = previews[conversation.id] || "";
            return (
              <div
                key={conversation.id}
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
              </div>
            );
          })
        )}
      </div>
    </aside>
  );
}
