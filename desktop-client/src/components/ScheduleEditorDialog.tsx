import { FormEvent, useEffect, useState } from "react";
import { CalendarPlus, X } from "@phosphor-icons/react";

import type { WorkspaceScheduleDraft } from "../types";

type ScheduleEditorDialogProps = {
  open: boolean;
  onClose: () => void;
  onSubmit: (draft: WorkspaceScheduleDraft) => Promise<void>;
};

type ScheduleFormState = {
  title: string;
  startTime: string;
  endTime: string;
  location: string;
  content: string;
};

/** 把数字补齐为日期输入框需要的两位格式。 */
function pad(value: number) {
  return String(value).padStart(2, "0");
}

/** 将本地时间转换为 datetime-local 输入值，不做 UTC 时区换算。 */
function toDateTimeLocalValue(value: Date) {
  return `${value.getFullYear()}-${pad(value.getMonth() + 1)}-${pad(value.getDate())}T${pad(value.getHours())}:${pad(value.getMinutes())}`;
}

/** 生成一个接近当前时刻的默认区间，减少用户创建近期日程时的输入量。 */
function createInitialForm(): ScheduleFormState {
  const start = new Date();
  start.setSeconds(0, 0);
  start.setMinutes(start.getMinutes() < 30 ? 30 : 0);
  if (start.getMinutes() === 0) start.setHours(start.getHours() + 1);
  const end = new Date(start.getTime() + 60 * 60 * 1000);
  return {
    title: "",
    startTime: toDateTimeLocalValue(start),
    endTime: toDateTimeLocalValue(end),
    location: "",
    content: "",
  };
}

/** 把浏览器本地时间输入转换为 Spring 接口约定的无时区日期格式。 */
function toApiDateTime(value: string) {
  const normalized = value.trim().replace("T", " ");
  return normalized.length === 16 ? `${normalized}:00` : normalized;
}

/** 渲染手动新增日程弹窗，并在提交前完成必填项和时间区间校验。 */
export function ScheduleEditorDialog({ open, onClose, onSubmit }: ScheduleEditorDialogProps) {
  const [form, setForm] = useState<ScheduleFormState>(createInitialForm);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState("");

  /** 每次重新打开都恢复一份干净表单，避免上次未提交内容残留。 */
  useEffect(() => {
    if (!open) return;
    setForm(createInitialForm());
    setSaving(false);
    setError("");
  }, [open]);

  /** 允许按 Esc 关闭弹窗，但保存过程中不打断正在发送的请求。 */
  useEffect(() => {
    if (!open) return undefined;
    const handleKeyDown = (event: KeyboardEvent) => {
      if (event.key === "Escape" && !saving) onClose();
    };
    window.addEventListener("keydown", handleKeyDown);
    return () => window.removeEventListener("keydown", handleKeyDown);
  }, [open, saving, onClose]);

  /** 校验表单并调用工作台日程接口，失败信息直接保留在弹窗中。 */
  async function submitSchedule(event: FormEvent) {
    event.preventDefault();
    const title = form.title.trim();
    if (!title) {
      setError("请填写日程事件名称");
      return;
    }
    if (!form.startTime) {
      setError("请选择开始时间");
      return;
    }
    if (form.endTime && new Date(form.endTime).getTime() < new Date(form.startTime).getTime()) {
      setError("结束时间不能早于开始时间");
      return;
    }

    setSaving(true);
    setError("");
    try {
      await onSubmit({
        title,
        startTime: toApiDateTime(form.startTime),
        endTime: form.endTime ? toApiDateTime(form.endTime) : null,
        location: form.location.trim() || null,
        content: form.content.trim() || null,
      });
      onClose();
    } catch (submitError) {
      setError(submitError instanceof Error ? submitError.message : "日程创建失败");
    } finally {
      setSaving(false);
    }
  }

  if (!open) return null;

  return <div className="schedule-editor-backdrop" onMouseDown={() => !saving && onClose()}>
    <section className="schedule-editor-dialog" role="dialog" aria-modal="true" aria-label="手动添加日程" onMouseDown={(event) => event.stopPropagation()}>
      <header>
        <div><span><CalendarPlus size={19} /></span><div><small>NEW SCHEDULE</small><h3>手动添加日程</h3></div></div>
        <button type="button" aria-label="关闭添加日程弹窗" onClick={onClose} disabled={saving}><X size={18} /></button>
      </header>
      <form onSubmit={submitSchedule}>
        <label className="schedule-editor-wide"><span>事件名称</span><input autoFocus value={form.title} onChange={(event) => setForm((current) => ({ ...current, title: event.target.value }))} placeholder="例如：项目例会" maxLength={120} /></label>
        <label><span>开始时间</span><input type="datetime-local" value={form.startTime} onChange={(event) => setForm((current) => ({ ...current, startTime: event.target.value }))} /></label>
        <label><span>结束时间</span><input type="datetime-local" value={form.endTime} onChange={(event) => setForm((current) => ({ ...current, endTime: event.target.value }))} /></label>
        <label className="schedule-editor-wide"><span>地点</span><input value={form.location} onChange={(event) => setForm((current) => ({ ...current, location: event.target.value }))} placeholder="可选，例如 A01-N105" maxLength={200} /></label>
        <label className="schedule-editor-wide"><span>补充说明</span><textarea value={form.content} onChange={(event) => setForm((current) => ({ ...current, content: event.target.value }))} placeholder="可选，记录需要准备的材料或日程背景" maxLength={2000} /></label>
        {error && <p className="schedule-editor-error" role="alert">{error}</p>}
        <footer><p>手动创建的日程会标记为本地来源，过期后由服务自动清理。</p><div><button className="secondary" type="button" onClick={onClose} disabled={saving}>取消</button><button className="primary" type="submit" disabled={saving}>{saving ? "保存中" : "保存日程"}</button></div></footer>
      </form>
    </section>
  </div>;
}
