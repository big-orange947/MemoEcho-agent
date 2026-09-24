/* =============================================================================
 * ContactsScreen.tsx - 通讯录（把 QQ 里的人与本地值守状态对上）
 * -----------------------------------------------------------------------------
 * 要解决的问题：会话列表只包含"消息流经过的会话"，所以看起来跟 QQ 好友/群
 * **对不上** —— 没聊过的人根本不会出现，于是"想值守某人"这件事无从下手。
 *
 * 这一屏把两边拼起来：
 *   QQ 里有哪些人（拉 NapCat）+ 我这边对谁开了值守（本地会话表）
 * 每一行给一个状态：未建会话 / 已建但全关 / 值守中，并支持勾选后批量开启。
 *
 * 建会话 = POST /api/conversations/resolve（带备注名，之后列表里显示的就是名字）；
 * 开策略 = PATCH /api/conversations/{id} —— 逐个做，才能回报"哪个失败了"。
 * ========================================================================== */

import { useEffect, useMemo, useState } from "react";
import {
  ContactEntry,
  ContactsResponse,
  getContacts,
  patchConversation,
  resolveConversation,
} from "../lib/api";
import { REPLY_MODE_LABEL } from "../lib/format";
import { Badge, Button, Empty, Section } from "../components/ui";

type Target = { entry: ContactEntry; chatType: "private" | "group" };

const PRESETS: { label: string; policy: Record<string, unknown>; hint: string }[] = [
  { label: "只建会话", policy: {}, hint: "建出来但全关：不监视、不回复、不上报" },
  { label: "监视", policy: { monitor: true }, hint: "只落库，不回复" },
  { label: "草稿待确认", policy: { reply_mode: "draft" }, hint: "拟好回复但不发出，等你在队列里确认" },
  { label: "自动回复", policy: { reply_mode: "auto" }, hint: "直接替你回复（建议先设注意事项）" },
  { label: "监视 + 上报", policy: { monitor: true, alert_enabled: true }, hint: "重要消息进上报队列" },
];

export function ContactsScreen({
  notify,
  onChanged,
}: {
  notify: (message: string, tone?: "info" | "danger") => void;
  onChanged: () => void;
}) {
  const [data, setData] = useState<ContactsResponse | null>(null);
  const [loading, setLoading] = useState(true);
  const [query, setQuery] = useState("");
  const [selected, setSelected] = useState<string[]>([]);
  const [busy, setBusy] = useState(false);

  async function load() {
    setLoading(true);
    try {
      setData(await getContacts());
    } catch (error: any) {
      notify(`读取通讯录失败：${error.message}`, "danger");
    } finally {
      setLoading(false);
    }
  }

  useEffect(() => {
    void load();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const all = useMemo<Target[]>(() => {
    if (!data) return [];
    return [
      ...data.friends.map((entry) => ({ entry, chatType: "private" as const })),
      ...data.groups.map((entry) => ({ entry, chatType: "group" as const })),
    ];
  }, [data]);

  const filtered = useMemo(() => {
    const keyword = query.trim().toLowerCase();
    if (!keyword) return all;
    return all.filter(
      (item) =>
        item.entry.title.toLowerCase().includes(keyword) ||
        item.entry.external_id.includes(keyword)
    );
  }, [all, query]);

  const friends = filtered.filter((item) => item.chatType === "private");
  const groups = filtered.filter((item) => item.chatType === "group");

  function keyOf(item: Target) {
    return `${item.chatType}:${item.entry.external_id}`;
  }

  function toggle(item: Target) {
    const key = keyOf(item);
    setSelected((prev) => (prev.includes(key) ? prev.filter((k) => k !== key) : [...prev, key]));
  }

  async function apply(preset: (typeof PRESETS)[number]) {
    const targets = all.filter((item) => selected.includes(keyOf(item)));
    if (!targets.length || busy) return;
    setBusy(true);

    let created = 0;
    let patched = 0;
    const failed: string[] = [];
    for (const item of targets) {
      const label = item.entry.title || item.entry.external_id;
      try {
        let conversationId = item.entry.conversation?.conversation_id || "";
        if (!conversationId) {
          const conversation = await resolveConversation({
            platform: "qq",
            chat_type: item.chatType,
            external_id: item.entry.external_id,
            title: item.entry.title || undefined,
          });
          conversationId = conversation.id;
          created += 1;
        }
        if (Object.keys(preset.policy).length) {
          await patchConversation(conversationId, preset.policy);
          patched += 1;
        }
      } catch (error: any) {
        failed.push(`${label}: ${error.message}`);
      }
    }

    setBusy(false);
    setSelected([]);
    notify(
      failed.length
        ? `${preset.label}：新建 ${created}、改策略 ${patched}，失败 ${failed.length}（${failed[0]}）`
        : `${preset.label}：新建 ${created} 个会话、改策略 ${patched} 个`,
      failed.length ? "danger" : "info"
    );
    await load();
    onChanged();
  }

  return (
    <div className="main">
      <div className="scroll">
        <Section
          title="通讯录"
          actions={
            <span className="row" style={{ gap: 8 }}>
              {data?.ok ? (
                <span className="mono">
                  自己 {data.bot.nickname || ""} {data.bot.user_id}
                </span>
              ) : null}
              <Button size="sm" variant="ghost" onClick={load} disabled={loading}>
                {loading ? "读取中…" : "刷新"}
              </Button>
            </span>
          }
        >
          {!data ? (
            <Empty>读取中…</Empty>
          ) : !data.ok ? (
            <div className="stack">
              <Badge tone="warn">NapCat 未连接</Badge>
              <div className="hint">
                {data.error || "无法连接 NapCat"} —— 通讯录要连上 QQ 才能读。
                启动 NapCat（登录 QQ）后点"刷新"。
              </div>
              <div className="hint">
                没连上也不影响其它页面：会话列表、上报队列、策略配置都读本地库。
              </div>
            </div>
          ) : (
            <div className="stack">
              <div className="row" style={{ gap: 8, flexWrap: "wrap" }}>
                <span className="chip">好友 {data.counts.friends}</span>
                <span className="chip">群 {data.counts.groups}</span>
                <span className="chip">已建会话 {data.counts.managed}</span>
                <span className="spacer" />
                <input
                  className="input"
                  style={{ width: 200 }}
                  placeholder="按名字或号码筛选"
                  value={query}
                  onChange={(event) => setQuery(event.target.value)}
                />
              </div>

              <div className="row" style={{ gap: 6, flexWrap: "wrap" }}>
                {PRESETS.map((preset) => (
                  <Button
                    key={preset.label}
                    size="sm"
                    disabled={busy || !selected.length}
                    title={preset.hint}
                    onClick={() => apply(preset)}
                  >
                    {preset.label}
                    {selected.length ? ` (${selected.length})` : ""}
                  </Button>
                ))}
                <Button
                  size="sm"
                  variant="ghost"
                  onClick={() =>
                    setSelected(
                      selected.length === filtered.length ? [] : filtered.map(keyOf)
                    )
                  }
                >
                  {selected.length === filtered.length && filtered.length ? "取消全选" : "全选当前筛选"}
                </Button>
              </div>

              <div className="hint">
                勾选后点上面任一预设：没建过会话的会先建出来，然后套用策略。
                建出来的会话会立刻出现在左侧列表里。
              </div>
            </div>
          )}
        </Section>

        {data?.ok ? (
          <>
            <ContactGroup
              title="好友"
              items={friends}
              selected={selected}
              onToggle={toggle}
              empty="没有匹配的好友。"
            />
            <ContactGroup
              title="群"
              items={groups}
              selected={selected}
              onToggle={toggle}
              empty="没有匹配的群。"
            />
          </>
        ) : null}
      </div>
    </div>
  );
}

function ContactGroup({
  title,
  items,
  selected,
  onToggle,
  empty,
}: {
  title: string;
  items: Target[];
  selected: string[];
  onToggle: (item: Target) => void;
  empty: string;
}) {
  return (
    <Section title={title} actions={<span className="mono">{items.length}</span>}>
      {items.length === 0 ? (
        <div className="hint">{empty}</div>
      ) : (
        <div className="stack" style={{ gap: 4 }}>
          {items.map((item) => {
            const key = `${item.chatType}:${item.entry.external_id}`;
            const conversation = item.entry.conversation;
            const memberCount = item.entry.raw?.member_count;
            return (
              <label key={key} className="tool" data-on={selected.includes(key)}>
                <input
                  type="checkbox"
                  checked={selected.includes(key)}
                  onChange={() => onToggle(item)}
                />
                <span className="name">{item.entry.title || "(无备注)"}</span>
                <span className="mono">{item.entry.external_id}</span>
                {memberCount ? <span className="mono">{memberCount} 人</span> : null}
                <span className="spacer" />
                {!conversation ? (
                  <Badge>未建会话</Badge>
                ) : conversation.monitor ? (
                  <Badge tone={conversation.alert_enabled ? "urgent" : "ok"}>
                    值守中 · {REPLY_MODE_LABEL[conversation.reply_mode]}
                    {conversation.alert_enabled ? " · 上报" : ""}
                  </Badge>
                ) : (
                  <Badge tone="warn">已建会话 · 全关</Badge>
                )}
              </label>
            );
          })}
        </div>
      )}
    </Section>
  );
}
