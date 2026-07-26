import { useEffect, useRef, useState, type ChangeEvent } from "react";
import { invoke } from "@tauri-apps/api/core";
import type { QceImportPreview } from "../types";

type QceExportFile = {
  path: string;
  name: string;
  modifiedAt: number;
  size: number;
};

type QceHistoryImporterProps = {
  fileName: string;
  preview: QceImportPreview | null;
  chatIdOverride: string;
  chatTypeOverride: string;
  onFileSelected: (event: ChangeEvent<HTMLInputElement>) => void;
  onChatIdOverrideChange: (value: string) => void;
  onChatTypeOverrideChange: (value: string) => void;
  onPreview: () => void;
  onImport: () => void;
  onAutoImport: (exportData: unknown, sourceName: string) => Promise<{ importedCount: number; duplicateCount: number }>;
  onStatus: (value: string) => void;
  busy: boolean;
};

/**
 * 引导用户从 QCE 导入自己主动导出的历史记录。
 *
 * <p>组件只读取用户手动选择的 JSON 文件，不具备目录扫描能力；所有导入记录都会在后端
 * 标记为 HISTORY_IMPORT，因而不会被实时自动回复和消息通知链路处理。</p>
 */
export function QceHistoryImporter({
  fileName,
  preview,
  chatIdOverride,
  chatTypeOverride,
  onFileSelected,
  onChatIdOverrideChange,
  onChatTypeOverrideChange,
  onPreview,
  onImport,
  onAutoImport,
  onStatus,
  busy,
}: QceHistoryImporterProps) {
  const [watchDirectory, setWatchDirectory] = useState(() => localStorage.getItem("memo-echo-qce-watch-directory") || "");
  const [autoImportEnabled, setAutoImportEnabled] = useState(false);
  const [watchStatus, setWatchStatus] = useState("");
  const scanningRef = useRef(false);

  /** 从操作系统文件夹选择器读取 QCE 导出目录，只在 Tauri 客户端中可用。 */
  async function selectWatchDirectory() {
    try {
      const directory = await invoke<string | null>("pick_qce_export_directory");
      if (!directory) return;
      setWatchDirectory(directory);
      localStorage.setItem("memo-echo-qce-watch-directory", directory);
      setWatchStatus("已选择目录。开启自动导入后会每 30 秒检查一次新增 JSON");
    } catch (error) {
      setWatchStatus(error instanceof Error ? error.message : "无法打开目录选择器");
    }
  }

  /** 扫描目录并按文件路径和修改时间去重，避免同一份 QCE 导出被反复提交。 */
  async function scanAndImport() {
    if (!autoImportEnabled || !watchDirectory || busy || scanningRef.current) return;
    scanningRef.current = true;
    try {
      const files = await invoke<QceExportFile[]>("list_qce_export_files", { directory: watchDirectory });
      const stored = JSON.parse(localStorage.getItem("memo-echo-qce-imported-files") || "[]") as string[];
      const known = new Set(stored);
      const pendingFiles = [...files]
        .sort((left, right) => left.modifiedAt - right.modifiedAt)
        .filter((file) => !known.has(`${file.path}:${file.modifiedAt}`));
      if (pendingFiles.length === 0) {
        setWatchStatus("目录已检查，没有新的 QCE JSON");
        return;
      }

      for (const file of pendingFiles) {
        const content = await invoke<string>("read_qce_export_file", { path: file.path });
        const exportData = JSON.parse(content) as unknown;
        const result = await onAutoImport(exportData, file.name);
        known.add(`${file.path}:${file.modifiedAt}`);
        setWatchStatus(`已自动导入 ${file.name}：${result.importedCount} 条新增，${result.duplicateCount} 条重复`);
      }
      localStorage.setItem("memo-echo-qce-imported-files", JSON.stringify([...known].slice(-1000)));
    } catch (error) {
      const message = error instanceof Error ? error.message : "QCE 自动导入失败";
      setWatchStatus(message);
      onStatus(`QCE 自动导入未完成：${message}`);
    } finally {
      scanningRef.current = false;
    }
  }

  useEffect(() => {
    if (!autoImportEnabled || !watchDirectory) return;
    void scanAndImport();
    const timer = window.setInterval(() => void scanAndImport(), 30_000);
    return () => window.clearInterval(timer);
  }, [autoImportEnabled, watchDirectory, busy]);

  return <section className="panel qce-import-panel">
    <div className="panel-title">
      <div>
        <p className="eyebrow">HISTORY IMPORT</p>
        <h2>导入 QQ 历史记录</h2>
        <span>仅支持 QQ Chat Exporter 的单文件 JSON。导入只作为上下文和检索素材，不会自动回复。</span>
      </div>
    </div>
    <div className="qce-watch-panel">
      <div>
        <b>自动导入 QCE 增量备份</b>
        <span>客户端运行期间每 30 秒扫描一次；仅导入新的 JSON，不会触发自动回复</span>
      </div>
      <div className="qce-watch-actions">
        <button className="outline-button" type="button" onClick={() => void selectWatchDirectory()}>选择导出目录</button>
        <button className={autoImportEnabled ? "danger-button" : "outline-button"} type="button" disabled={!watchDirectory} onClick={() => setAutoImportEnabled((value) => !value)}>{autoImportEnabled ? "停止自动导入" : "开启自动导入"}</button>
      </div>
      <small>{watchDirectory || "尚未选择 QCE 的 JSON 导出目录"}</small>
      {watchStatus && <small className="qce-watch-status">{watchStatus}</small>}
    </div>
    <div className="qce-import-controls">
      <label className="file-picker">选择 QCE JSON<input type="file" accept="application/json,.json" onChange={onFileSelected} /></label>
      <span>{fileName || "尚未选择文件"}</span>
    </div>
    {fileName && <>
      <div className="qce-mapping">
        <label>会话类型<select value={chatTypeOverride} onChange={(event) => onChatTypeOverrideChange(event.target.value)}><option value="">使用文件识别结果</option><option value="private">QQ 私聊</option><option value="group">QQ 群聊</option></select></label>
        <label>会话 ID / QQ 号 / 群号<input value={chatIdOverride} onChange={(event) => onChatIdOverrideChange(event.target.value)} placeholder="群聊导出缺少群号时必填" /></label>
        <button className="outline-button" type="button" disabled={busy} onClick={onPreview}>生成预览</button>
      </div>
      {preview && <div className="qce-preview">
        <div className="qce-preview-head">
          <div><b>{preview.chatName}</b><span>{preview.detectedChatType} · {preview.detectedChatId || "需要映射会话"}</span></div>
          <button type="button" disabled={busy || preview.requiresChatIdMapping} onClick={onImport}>确认导入</button>
        </div>
        <div className="qce-stats"><span>{preview.totalMessages} 条消息</span><span>{preview.textMessages} 条文本</span><span>{preview.attachmentMessages} 条含附件</span><span>{preview.imageAttachments} 图 / {preview.videoAttachments} 视频 / {preview.audioAttachments} 音频 / {preview.fileAttachments} 文件</span></div>
        {preview.requiresChatIdMapping && <p className="qce-warning">此导出未包含群号。请填写正确群号后重新生成预览，防止历史记录进入错误会话。</p>}
        {preview.warnings.map((warning) => <p className="qce-warning" key={warning}>{warning}</p>)}
        <div className="qce-samples">{preview.samples.map((sample) => <p key={sample.messageId}><b>{sample.senderName}</b><span>{sample.text}</span>{sample.attachmentCount > 0 && <small>附件 {sample.attachmentCount}</small>}</p>)}</div>
      </div>}
    </>}
  </section>;
}
