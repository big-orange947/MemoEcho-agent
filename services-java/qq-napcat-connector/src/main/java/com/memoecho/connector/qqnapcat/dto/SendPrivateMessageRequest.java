package com.memoecho.connector.qqnapcat.dto;

import jakarta.validation.constraints.AssertTrue;
import jakarta.validation.constraints.NotNull;

import java.util.List;

public record SendPrivateMessageRequest(
        @NotNull Long userId,
        String message,
        List<MessageSegmentPayload> segments,
        String clientMessageId,
        String correlationId
) {

    /** 兼容旧客户端的三参数构造方式。 */
    public SendPrivateMessageRequest(Long userId, String message, List<MessageSegmentPayload> segments) {
        this(userId, message, segments, null, null);
    }

    @AssertTrue(message = "message 和 segments 必须二选一且至少提供一个")
    public boolean hasValidMessageBody() {
        return hasTextMessage() ^ hasSegmentsMessage();
    }

    public Object toNapcatMessage() {
        if (hasSegmentsMessage()) {
            return segments.stream().map(MessageSegmentPayload::toMap).toList();
        }
        return message;
    }

    private boolean hasTextMessage() {
        return message != null && !message.isBlank();
    }

    private boolean hasSegmentsMessage() {
        return segments != null && !segments.isEmpty();
    }
}
