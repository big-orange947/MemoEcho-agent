/* =============================================================================
 * ui.tsx - 通用小组件
 * -----------------------------------------------------------------------------
 * 刻意不引组件库：这套 UI 的"性格"全在 CSS 里（玻璃、描边、等宽元信息），
 * 引一个库反而要花力气把它改回这个样子。这里只放会被复用三次以上的零件。
 * ========================================================================== */

import React from "react";

/* ------------------------------------------------------------------ 徽标 */
export function Badge({
  children,
  tone,
  title,
}: {
  children: React.ReactNode;
  tone?: string;
  title?: string;
}) {
  return (
    <span className="badge" data-tone={tone} title={title}>
      {children}
    </span>
  );
}

export function Dot({ tone, title }: { tone?: string; title?: string }) {
  return <span className="dot" data-tone={tone} title={title} />;
}

/* ------------------------------------------------------------------ 开关 */
export function Switch({
  checked,
  onChange,
  label,
  disabled,
  hint,
}: {
  checked: boolean;
  onChange: (next: boolean) => void;
  label: string;
  disabled?: boolean;
  hint?: string;
}) {
  return (
    <label className="switch" title={hint}>
      <input
        type="checkbox"
        checked={checked}
        disabled={disabled}
        onChange={(event) => onChange(event.target.checked)}
      />
      <span>{label}</span>
    </label>
  );
}

/* ------------------------------------------------------------------ 表单 */
export function Field({
  label,
  hint,
  children,
}: {
  label: string;
  hint?: string;
  children: React.ReactNode;
}) {
  // 把 label 真正**关联**到控件上：点文字能聚焦、读屏器能念出字段名。
  // 只写一个视觉上的 <label> 是常见的假无障碍 —— 看着像有标签，其实没有。
  const id = React.useId();
  const control = React.isValidElement(children)
    ? React.cloneElement(children as React.ReactElement<{ id?: string }>, { id })
    : children;
  return (
    <div className="field">
      <label htmlFor={id}>{label}</label>
      {control}
      {hint ? <div className="hint">{hint}</div> : null}
    </div>
  );
}

export function Section({
  title,
  actions,
  children,
}: {
  title: string;
  actions?: React.ReactNode;
  children: React.ReactNode;
}) {
  return (
    <div className="section">
      <h3 className="section-title">
        {title}
        {actions ? <span className="spacer" /> : null}
        {actions}
      </h3>
      {children}
    </div>
  );
}

export function Empty({ children }: { children: React.ReactNode }) {
  return <div className="empty">{children}</div>;
}

/* ------------------------------------------------------------------ 提示条 */
export function Toast({
  message,
  tone,
}: {
  message: string;
  tone?: "info" | "danger";
}) {
  if (!message) return null;
  return (
    // role=status + aria-live: 读屏器会把异步提示念出来（否则视障用户永远不知道
    // "已保存""发送失败"发生过 —— 这类反馈不落到 DOM 焦点上）
    <div className="toast" data-tone={tone} role="status" aria-live="polite">
      <Dot tone={tone === "danger" ? "danger" : "accent"} />
      {message}
    </div>
  );
}

/* ------------------------------------------------------------------ 键值表 */
export function KV({ items }: { items: [string, React.ReactNode][] }) {
  return (
    <dl className="kv">
      {items.map(([key, value]) => (
        <React.Fragment key={key}>
          <dt>{key}</dt>
          <dd>{value}</dd>
        </React.Fragment>
      ))}
    </dl>
  );
}

/* ------------------------------------------------------------------ 按钮 */
export function Button({
  children,
  onClick,
  variant = "default",
  size,
  disabled,
  title,
}: {
  children: React.ReactNode;
  onClick?: () => void;
  variant?: "default" | "primary" | "ghost" | "danger";
  size?: "sm";
  disabled?: boolean;
  title?: string;
}) {
  return (
    <button
      className="btn"
      data-variant={variant}
      data-size={size}
      disabled={disabled}
      title={title}
      onClick={onClick}
    >
      {children}
    </button>
  );
}
