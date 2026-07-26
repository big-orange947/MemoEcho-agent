import { FormEvent, useEffect, useState } from "react";
import { Archive, Check, PencilSimple, Plus, ShieldCheck, Trash, X } from "@phosphor-icons/react";
import type { ConversationProfileContext, SecureAsset, SecureAssetDraft } from "../types";

const EMPTY_ASSET: SecureAssetDraft = {
  name: "",
  type: "PAYMENT_CODE",
  description: "",
  contentType: "text/plain",
  content: "",
  usagePolicy: "REUSABLE",
  remainingUses: null,
  enabled: true,
};

const ASSET_TYPES = [
  ["PAYMENT_CODE", "收款码"],
  ["LICENSE_CODE", "卡密 / 激活码"],
  ["IMAGE", "图片"],
  ["FILE", "文件或下载地址"],
  ["TEXT_SECRET", "敏感文本"],
  ["OTHER", "其他"],
] as const;

type AssetReference = ConversationProfileContext["assets"][number];

/** 把后端资产元数据转换成会话设定中的安全引用，不复制任何敏感正文。 */
function toReference(asset: SecureAsset): AssetReference {
  return {
    assetId: asset.id,
    type: asset.type,
    name: asset.name,
    description: asset.description,
    usageCondition: "",
  };
}

/** 根据保存策略生成面向用户的资产可用性说明。 */
function describeInventory(asset: SecureAsset) {
  if (!asset.enabled) return "已停用";
  if (asset.usagePolicy === "SINGLE_USE") return `单次使用 · 剩余 ${asset.remainingUses ?? 0}`;
  return "可重复使用";
}

/**
 * 渲染 Profile 2.0 的资产选择区。
 * 这里只保存 assetId 和使用条件，真实正文只能由受信任 Runtime 在执行阶段解析。
 */
export function SecureAssetReferenceEditor({
  assets,
  references,
  onChange,
  onOpenManager,
}: {
  assets: SecureAsset[];
  references: AssetReference[];
  onChange: (references: AssetReference[]) => void;
  onOpenManager: () => void;
}) {
  /** 在当前设定中添加或移除资产引用。 */
  function toggleAsset(asset: SecureAsset) {
    const selected = references.some((reference) => reference.assetId === asset.id);
    onChange(selected
      ? references.filter((reference) => reference.assetId !== asset.id)
      : [...references, toReference(asset)]);
  }

  /** 仅更新会话级使用条件，不修改资产库元数据和密文正文。 */
  function updateCondition(assetId: string, usageCondition: string) {
    onChange(references.map((reference) => reference.assetId === assetId
      ? { ...reference, usageCondition }
      : reference));
  }

  const missingReferences = references.filter((reference) => !assets.some((asset) => asset.id === reference.assetId));
  return <div className="secure-asset-reference-editor">
    <header><div><b>可用资产</b><small>选择 Runtime 可以按规则取用的加密资产</small></div><button type="button" onClick={onOpenManager}><Archive size={16} />管理资产库</button></header>
    {assets.length === 0 ? <div className="secure-asset-empty"><ShieldCheck size={20} /><span>资产库为空。先创建收款码、卡密或文件资产。</span></div> : <div className="secure-asset-picker">
      {assets.map((asset) => {
        const reference = references.find((item) => item.assetId === asset.id);
        const unavailable = !asset.enabled || (asset.usagePolicy === "SINGLE_USE" && (asset.remainingUses ?? 0) <= 0);
        return <article className={reference ? "selected" : ""} key={asset.id}>
          <button type="button" disabled={unavailable && !reference} onClick={() => toggleAsset(asset)}>
            <span className="secure-asset-check">{reference ? <Check size={13} weight="bold" /> : <Plus size={13} />}</span>
            <span><b>{asset.name}</b><small>{describeInventory(asset)} · {asset.type}</small></span>
          </button>
          {reference && <label>本会话使用条件<input value={reference.usageCondition} onChange={(event) => updateCondition(asset.id, event.target.value)} placeholder="例如：买家确认付款方式且审核通过后" /></label>}
        </article>;
      })}
    </div>}
    {missingReferences.map((reference) => <div className="secure-asset-missing" key={reference.assetId}>引用“{reference.name || reference.assetId}”已不存在<button type="button" onClick={() => onChange(references.filter((item) => item.assetId !== reference.assetId))}>移除</button></div>)}
  </div>;
}

/**
 * 渲染安全资产管理弹窗。
 * 已保存正文永不回填到表单；编辑时只有用户主动输入新正文才会替换服务器密文。
 */
export function SecureAssetManagerDialog({ open, assets, busy, onClose, onSave, onDelete }: {
  open: boolean;
  assets: SecureAsset[];
  busy: boolean;
  onClose: () => void;
  onSave: (assetId: string, draft: SecureAssetDraft) => Promise<void>;
  onDelete: (asset: SecureAsset) => Promise<void>;
}) {
  const [editingId, setEditingId] = useState("");
  const [draft, setDraft] = useState<SecureAssetDraft>(EMPTY_ASSET);
  const [feedback, setFeedback] = useState("");

  /** 弹窗关闭时清理敏感输入，避免正文继续留在 React 内存状态中。 */
  useEffect(() => {
    if (!open) {
      setEditingId("");
      setDraft(EMPTY_ASSET);
      setFeedback("");
    }
  }, [open]);

  /** 打开编辑状态，但不从后端尝试读取或回填已经加密的正文。 */
  function editAsset(asset: SecureAsset) {
    setEditingId(asset.id);
    setDraft({
      name: asset.name,
      type: asset.type,
      description: asset.description,
      contentType: asset.contentType,
      content: null,
      usagePolicy: asset.usagePolicy,
      remainingUses: asset.remainingUses,
      enabled: asset.enabled,
    });
    setFeedback("正文留空会保留原加密内容；输入新正文才会替换。");
  }

  /** 重置为新建模式，并清除上一条资产可能残留的敏感输入。 */
  function createAsset() {
    setEditingId("");
    setDraft(EMPTY_ASSET);
    setFeedback("");
  }

  /** 校验并保存资产，成功后立即清除正文输入。 */
  async function submit(event: FormEvent) {
    event.preventDefault();
    if (!editingId && !draft.content?.trim()) {
      setFeedback("新建资产必须填写正文或数据地址。");
      return;
    }
    try {
      await onSave(editingId, {
        ...draft,
        remainingUses: draft.usagePolicy === "SINGLE_USE" ? Math.max(1, draft.remainingUses ?? 1) : null,
      });
      setEditingId("");
      setDraft(EMPTY_ASSET);
      setFeedback("资产已加密保存。");
    } catch (error) {
      setFeedback(error instanceof Error ? error.message : "保存资产失败");
    }
  }

  if (!open) return null;
  return <div className="secure-asset-backdrop" onMouseDown={(event) => { event.stopPropagation(); if (event.target === event.currentTarget) onClose(); }}>
    <section className="secure-asset-dialog" role="dialog" aria-modal="true" aria-label="安全资产库" onMouseDown={(event) => event.stopPropagation()}>
      <header><div><p>SECURE ASSET VAULT</p><h3>安全资产库</h3><span>敏感正文加密保存在本地服务，客户端只显示元数据。</span></div><button type="button" onClick={onClose}><X size={18} /></button></header>
      <div className="secure-asset-layout">
        <aside><button className="secure-asset-new" type="button" onClick={createAsset}><Plus size={15} />新建资产</button>{assets.map((asset) => <article className={editingId === asset.id ? "active" : ""} key={asset.id}><button type="button" onClick={() => editAsset(asset)}><b>{asset.name}</b><small>{describeInventory(asset)}</small></button><div><button title="编辑" type="button" onClick={() => editAsset(asset)}><PencilSimple size={14} /></button><button title="删除" type="button" onClick={() => void onDelete(asset)}><Trash size={14} /></button></div></article>)}</aside>
        <form onSubmit={submit}>
          <div className="secure-asset-form-grid">
            <label>资产名称<input required maxLength={255} value={draft.name} onChange={(event) => setDraft({ ...draft, name: event.target.value })} placeholder="例如：微信收款码" /></label>
            <label>资产类型<select value={draft.type} onChange={(event) => setDraft({ ...draft, type: event.target.value })}>{ASSET_TYPES.map(([value, label]) => <option value={value} key={value}>{label}</option>)}</select></label>
            <label className="full">说明<input maxLength={2000} value={draft.description} onChange={(event) => setDraft({ ...draft, description: event.target.value })} placeholder="说明用途，不要在这里重复填写秘密正文" /></label>
            <label>正文格式<input value={draft.contentType} onChange={(event) => setDraft({ ...draft, contentType: event.target.value })} placeholder="text/plain 或 image/png" /></label>
            <label>使用策略<select value={draft.usagePolicy} onChange={(event) => setDraft({ ...draft, usagePolicy: event.target.value as SecureAssetDraft["usagePolicy"], remainingUses: event.target.value === "SINGLE_USE" ? (draft.remainingUses ?? 1) : null })}><option value="REUSABLE">可重复使用</option><option value="SINGLE_USE">单次库存</option></select></label>
            {draft.usagePolicy === "SINGLE_USE" && <label>剩余次数<input type="number" min={1} value={draft.remainingUses ?? 1} onChange={(event) => setDraft({ ...draft, remainingUses: Number(event.target.value) })} /></label>}
            <label className="secure-asset-enabled"><input type="checkbox" checked={draft.enabled} onChange={(event) => setDraft({ ...draft, enabled: event.target.checked })} />允许 Runtime 使用</label>
            <label className="full">{editingId ? "替换正文（可选）" : "敏感正文"}<textarea value={draft.content ?? ""} onChange={(event) => setDraft({ ...draft, content: event.target.value })} placeholder={editingId ? "留空即保留原加密正文" : "文本、卡密、data URL 或受控文件地址"} /></label>
          </div>
          <p className="secure-asset-warning"><ShieldCheck size={15} />普通客户端之后无法读取已保存正文。单次库存会在 Runtime 成功解析时原子扣减。</p>
          {feedback && <p className="secure-asset-feedback">{feedback}</p>}
          <footer><button type="button" onClick={createAsset}>清空</button><button className="primary" type="submit" disabled={busy}>{busy ? "保存中…" : editingId ? "保存修改" : "加密保存"}</button></footer>
        </form>
      </div>
    </section>
  </div>;
}
