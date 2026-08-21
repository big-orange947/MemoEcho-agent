import { KeyboardEvent } from "react";
import { PaperPlaneRight } from "@phosphor-icons/react";

/** 主控台聊天输入框：Enter 发送、Shift+Enter 换行。 */
export function Composer({
  value,
  onChange,
  onSend,
  busy,
}: {
  value: string;
  onChange: (value: string) => void;
  onSend: () => void;
  busy: boolean;
}) {
  function handleKeyDown(event: KeyboardEvent<HTMLTextAreaElement>) {
    if (event.key === "Enter" && !event.shiftKey && !event.nativeEvent.isComposing) {
      event.preventDefault();
      if (!busy && value.trim()) onSend();
    }
  }

  return (
    <div className="ws-composer">
      <textarea
        value={value}
        onChange={(event) => onChange(event.target.value)}
        onKeyDown={handleKeyDown}
        aria-label="向 Memo Echo 发送消息"
        placeholder="告诉 Memo Echo 你想处理的事情…（Enter 发送，Shift+Enter 换行）"
        rows={2}
      />
      <button type="button" disabled={busy || !value.trim()} onClick={onSend} aria-label="发送">
        <PaperPlaneRight size={17} weight="bold" />
      </button>
    </div>
  );
}