/* =============================================================================
 * ProfilesScreen.tsx - 设定集：批量策略 / 工具授权 / 全局配置
 * -----------------------------------------------------------------------------
 * 三块内容，对应三种"配置的粒度"：
 *   1. 批量策略 —— 一次给多个会话套同一套开关（新接一批群时最有用）；
 *   2. 工具授权 —— 每个会话能用哪些工具（默认集之外要显式授权，
 *      群聊默认不给 send_qq_message，这里能看见"默认"长什么样再决定要不要覆盖）；
 *   3. 全局配置 —— alert_contacts（白名单联系人）与 contact_aliases（称呼别名）。
 *
 * 批量改动是**逐个 PATCH**，不是一次请求带多会话 —— 后端没有批量接口，
 * 而且逐个改能拿到每个会话各自的 implied（被自动打开的开关），
 * 失败也能说清是哪个会话失败了。
 * ========================================================================== */

import React, { useEffect, useMemo, useState } from "react";
import {
  Conversation,
  ToolInfo,
  listConfigs,
  listTools,
  patchConversation,
  putConfig,
  resolveConversation,
} from "../lib/api";
import { REPLY_MODE_LABEL, conversationLabel } from "../lib/format";
import { Badge, Button, Empty, Field, Section } from "../components/ui";

export function ProfilesScreen({
  conversations,
  notify,
  onChanged,
}: {
  conversations: Conversation[];
  notify: (message: string, tone?: "info" | "danger") => void;
  onChanged: () => void;
}) {
  const [tools, setTools] = useState<ToolInfo[]>([]);
  const [configs, setConfigs] = useState<Record<string, string>>({});
  const [selected, setSelected] = useState<string[]>([]);
  const [busy, setBusy] = useState(false);
  const [toolTarget, setToolTarget] = useState<string>("");
  const [contacts, setContacts] = useState("");
  const [aliases, setAliases] = useState("");

  useEffect(() => {
    listTools()
      .then(setTools)
      .catch(() => setTools([]));
    listConfigs()
      .then((result) => {
        setConfigs(result.configs || {});
        setContacts(result.configs?.alert_contacts || "");
        setAliases(result.configs?.contact_aliases || "");
      })
      .catch((error) => notify(`加载全局配置失败：${error.message}`, "danger"));
  }, [notify]);

  useEffect(() => {
    if (!toolTarget && conversations.length) {
      setToolTarget(conversations[0].id);
    }
  }, [conversations, toolTarget]);

  const target = useMemo(
    () => conversations.find((item) => item.id === toolTarget) || null,
    [conversations, toolTarget]
  );

  function toggleSelected(id: string) {
    setSelected((prev) =>
      prev.includes(id) ? prev.filter((item) => item !== id) : [...prev, id]
    );
  }

  async function applyBatch(patch: Record<string, unknown>, label: string) {
    if (!selected.length || busy) return;
    setBusy(true);
    let ok = 0;
    const failed: string[] = [];
    for (const id of selected) {
      try {
        await patchConversation(id, patch);
        ok += 1;
      } catch (error: any) {
        const conversation = conversations.find((item) => item.id === id);
        failed.push(`${conversation ? conversationLabel(conversation) : id}: ${error.message}`);
      }
    }
    setBusy(false);
    notify(
      failed.length
        ? `${label}：成功 ${ok} 个，失败 ${failed.length} 个（${failed[0]}）`
        : `${label}：已应用到 ${ok} 个会话`,
      failed.length ? "danger" : "info"
    );
    onChanged();
  }

  async function saveTools(next: string[]) {
    if (!target) return;
    try {
      await patchConversation(target.id, { allowed_tools: next });
      notify(`已更新 ${conversationLabel(target)} 的工具授权`);
      onChanged();
    } catch (error: any) {
      notify(`保存失败：${error.message}`, "danger");
    }
  }

  async function saveConfig(key: string, value: string) {
    try {
      await putConfig(key, value);
      notify("已保存");
    } catch (error: any) {
      notify(`保存失败：${error.message}`, "danger");
    }
  }

  const targetAllowed = target?.policy.allowed_tools || [];
  const isGroup = target?.chat_type === "group";

  return (
    <div className="main">
      <div className="scroll">
        <Section
          title="批量策略"
          actions={
            <span className="mono">
              已选 {selected.length} / {conversations.length}
            </span>
          }
        >
          {conversations.length === 0 ? (
            <Empty>还没有会话。</Empty>
          ) : (
            <div className="stack">
              <div className="row" style={{ flexWrap: "wrap", gap: 6 }}>
                <Button size="sm" variant="ghost" onClick={() => setSelected(conversations.map((c) => c.id))}>
                  全选
                </Button>
                <Button size="sm" variant="ghost" onClick={() => setSelected([])}>
                  清空
                </Button>
                <Button
                  size="sm"
                  variant="ghost"
                  onClick={() =>
                    setSelected(
                      conversations.filter((c) => c.platform === "qq").map((c) => c.id)
                    )
                  }
                >
                  只选 QQ
                </Button>
                <Button
                  size="sm"
                  variant="ghost"
                  onClick={() =>
                    setSelected(
                      conversations.filter((c) => c.chat_type === "group").map((c) => c.id)
                    )
                  }
                >
                  只选群聊
                </Button>
              </div>

              <div className="stack" style={{ gap: 4, maxHeight: 220, overflowY: "auto" }}>
                {conversations.map((conversation) => (
                  <label
                    key={conversation.id}
                    className="tool"
                    data-on={selected.includes(conversation.id)}
                  >
                    <input
                      type="checkbox"
                      checked={selected.includes(conversation.id)}
                      onChange={() => toggleSelected(conversation.id)}
                    />
                    <span className="name">{conversationLabel(conversation)}</span>
                    <span className="spacer" />
                    <span className="mono">
                      {conversation.platform}/{conversation.chat_type}
                    </span>
                  </label>
                ))}
              </div>

              <div className="row" style={{ flexWrap: "wrap", gap: 6 }}>
                <Button
                  size="sm"
                  disabled={busy || !selected.length}
                  onClick={() => applyBatch({ monitor: true }, "开启监视")}
                >
                  开启监视
                </Button>
                <Button
                  size="sm"
                  disabled={busy || !selected.length}
                  onClick={() => applyBatch({ monitor: false, reply_mode: "off", alert_enabled: false }, "回到静默")}
                >
                  回到静默
                </Button>
                {(["off", "draft", "auto"] as const).map((mode) => (
                  <Button
                    key={mode}
                    size="sm"
                    disabled={busy || !selected.length}
                    onClick={() => applyBatch({ reply_mode: mode }, `回复方式 → ${REPLY_MODE_LABEL[mode]}`)}
                  >
                    {REPLY_MODE_LABEL[mode]}
                  </Button>
                ))}
                <Button
                  size="sm"
                  disabled={busy || !selected.length}
                  onClick={() => applyBatch({ alert_enabled: true }, "开启上报")}
                >
                  开启上报
                </Button>
                <Button
                  size="sm"
                  disabled={busy || !selected.length}
                  onClick={() => applyBatch({ require_human_confirmation: true }, "开启请示")}
                >
                  拿不准先请示
                </Button>
              </div>
              <div className="hint">
                注意：开启自动回复/上报会自动打开"监视"（不监视就没有数据）。后端会在返回值里
                明确回报被顺手打开的开关。
              </div>
            </div>
          )}
        </Section>

        <Section
          title="工具授权"
          actions={
            conversations.length ? (
              <select
                className="input"
                style={{ width: 200 }}
                value={toolTarget}
                onChange={(event) => setToolTarget(event.target.value)}
              >
                {conversations.map((conversation) => (
                  <option key={conversation.id} value={conversation.id}>
                    {conversationLabel(conversation)}
                  </option>
                ))}
              </select>
            ) : null
          }
        >
          {!target ? (
            <Empty>还没有会话。</Empty>
          ) : (
            <div className="stack">
              <div className="hint">
                留空 = 按会话类型取默认（{isGroup ? "群聊默认不给" : "私聊默认给全部"}高危工具）。
                勾选任意一项 = 显式指定，默认集不再生效。
              </div>
              <div className="tools">
                {tools.map((tool) => {
                  const on = targetAllowed.includes(tool.name);
                  const defaultOn = isGroup ? tool.default_for.group : tool.default_for.private;
                  return (
                    <label key={tool.name} className="tool" data-on={on} title={tool.description}>
                      <input
                        type="checkbox"
                        checked={on}
                        onChange={() => {
                          const next = on
                            ? targetAllowed.filter((name) => name !== tool.name)
                            : [...targetAllowed, tool.name];
                          void saveTools(next);
                        }}
                      />
                      <span className="name">{tool.name}</span>
                      <span className="spacer" />
                      {tool.high_risk ? <Badge tone="danger">高危</Badge> : null}
                      {!on && defaultOn ? <span className="mono">默认</span> : null}
                    </label>
                  );
                })}
                {tools.length === 0 ? <div className="hint">读不到工具清单（runtime 未启动？）</div> : null}
              </div>
              {targetAllowed.length ? (
                <div className="row">
                  <Button size="sm" variant="ghost" onClick={() => saveTools([])}>
                    清空，回到默认
                  </Button>
                </div>
              ) : null}
            </div>
          )}
        </Section>

        <Section title="全局配置">
          <div className="stack">
            <Field
              label="白名单联系人（QQ 号，逗号或换行分隔）"
              hint="名单里的人的每条消息都会成为上报候选 —— 有些人说的话天然重要"
            >
              <textarea
                className="textarea"
                value={contacts}
                onChange={(event) => setContacts(event.target.value)}
                onBlur={() => {
                  if (contacts !== (configs.alert_contacts || "")) saveConfig("alert_contacts", contacts);
                }}
              />
            </Field>
            <Field
              label="称呼别名（如「小号=我的另一个号」）"
              hint="供联系人解析使用；格式与后端 contact_aliases 约定一致"
            >
              <textarea
                className="textarea"
                value={aliases}
                onChange={(event) => setAliases(event.target.value)}
                onBlur={() => {
                  if (aliases !== (configs.contact_aliases || "")) saveConfig("contact_aliases", aliases);
                }}
              />
            </Field>
            <div className="hint">
              改完失焦即保存。全局配置对所有会话生效，改动会写审计。
            </div>
          </div>
        </Section>

        <Section title="还没有的会话">
          <NewConversation
            notify={notify}
            onCreated={(id) => {
              setToolTarget(id);
              onChanged();
            }}
          />
        </Section>
      </div>
    </div>
  );
}

/* ---------------------------------------------------------------------------
 * 用三元组把会话建出来
 * ---------------------------------------------------------------------------
 * 默认全关的会话**没有消息**，列表里就不会出现 ——
 * 所以"要值守一个还没说过话的会话"这件事，只能靠显式建行。
 */
function NewConversation({
  notify,
  onCreated,
}: {
  notify: (message: string, tone?: "info" | "danger") => void;
  onCreated: (id: string) => void;
}) {
  const [platform, setPlatform] = React.useState("qq");
  const [chatType, setChatType] = React.useState("private");
  const [externalId, setExternalId] = React.useState("");
  const [title, setTitle] = React.useState("");

  async function create() {
    if (!externalId.trim()) {
      notify("填一下外部 ID（QQ 号或群号）", "danger");
      return;
    }
    try {
      const conversation = await resolveConversation({
        platform,
        chat_type: chatType,
        external_id: externalId.trim(),
        title: title.trim() || undefined,
      });
      notify("会话已就绪，可以开始配置策略了");
      onCreated(conversation.id);
      setExternalId("");
      setTitle("");
    } catch (error: any) {
      notify(`创建失败：${error.message}`, "danger");
    }
  }

  return (
    <div className="stack">
      <div className="row" style={{ gap: 8, flexWrap: "wrap" }}>
        <select className="input" style={{ width: 96 }} value={platform} onChange={(e) => setPlatform(e.target.value)}>
          <option value="qq">QQ</option>
          <option value="desktop">桌面</option>
        </select>
        <select className="input" style={{ width: 108 }} value={chatType} onChange={(e) => setChatType(e.target.value)}>
          <option value="private">私聊</option>
          <option value="group">群聊</option>
          <option value="thread">线程</option>
        </select>
        <input
          className="input"
          style={{ width: 180 }}
          placeholder="外部 ID（QQ 号 / 群号）"
          value={externalId}
          onChange={(e) => setExternalId(e.target.value)}
        />
        <input
          className="input"
          style={{ width: 180 }}
          placeholder="备注名（可选）"
          value={title}
          onChange={(e) => setTitle(e.target.value)}
        />
        <Button onClick={create}>建好会话</Button>
      </div>
      <div className="hint">
        建出来的会话默认全关（不监视、不回复、不上报）。要值守它，先把上面的策略打开。
      </div>
    </div>
  );
}
