package com.memoecho.connector.qqnapcat.service;

import org.springframework.stereotype.Component;

import java.time.Duration;
import java.time.Instant;
import java.util.Map;
import java.util.Optional;
import java.util.concurrent.ConcurrentHashMap;

/**
 * 短期保存 Runtime 发往 NapCat 的消息身份。
 * Webhook 回显到达后可据此确认消息由 Agent 发送，而不是依赖脆弱的文本猜测。
 */
@Component
public class OutboundMessageRegistry {

    private static final Duration RETENTION = Duration.ofMinutes(10);
    private final Map<String, OutboundMessage> byPlatformMessageId = new ConcurrentHashMap<>();
    private final Map<String, OutboundMessage> pendingByFingerprint = new ConcurrentHashMap<>();

    /** 在调用 NapCat 前登记消息，覆盖“Webhook 早于 HTTP 响应返回”的竞态窗口。 */
    public void registerPending(
            String chatType,
            String chatId,
            String normalizedText,
            String clientMessageId,
            String correlationId
    ) {
        if (isBlank(clientMessageId)) {
            return;
        }
        cleanupExpired();
        OutboundMessage message = new OutboundMessage(
                clientMessageId,
                correlationId,
                null,
                Instant.now()
        );
        pendingByFingerprint.put(fingerprint(chatType, chatId, normalizedText), message);
    }

    /** NapCat 返回 message_id 后建立精确映射，并清理发送前的临时指纹。 */
    public void complete(
            String chatType,
            String chatId,
            String normalizedText,
            String platformMessageId,
            String clientMessageId,
            String correlationId
    ) {
        if (isBlank(clientMessageId) || isBlank(platformMessageId)) {
            return;
        }
        OutboundMessage message = new OutboundMessage(
                clientMessageId,
                correlationId,
                platformMessageId,
                Instant.now()
        );
        byPlatformMessageId.put(platformMessageId, message);
        pendingByFingerprint.remove(fingerprint(chatType, chatId, normalizedText));
        cleanupExpired();
    }

    /**
     * 优先按平台消息 ID 精确匹配；仅在发送响应尚未返回时使用一次性指纹匹配。
     */
    public Optional<OutboundMessage> resolve(
            String platformMessageId,
            String chatType,
            String chatId,
            String normalizedText
    ) {
        cleanupExpired();
        if (!isBlank(platformMessageId)) {
            OutboundMessage exact = byPlatformMessageId.get(platformMessageId);
            if (exact != null) {
                return Optional.of(exact);
            }
        }
        return Optional.ofNullable(
                pendingByFingerprint.remove(fingerprint(chatType, chatId, normalizedText))
        );
    }

    /** 删除过期关联，避免常驻 connector 的内存持续增长。 */
    private void cleanupExpired() {
        Instant threshold = Instant.now().minus(RETENTION);
        byPlatformMessageId.entrySet().removeIf(entry -> entry.getValue().registeredAt().isBefore(threshold));
        pendingByFingerprint.entrySet().removeIf(entry -> entry.getValue().registeredAt().isBefore(threshold));
    }

    private String fingerprint(String chatType, String chatId, String normalizedText) {
        return String.join("|", safe(chatType), safe(chatId), safe(normalizedText));
    }

    private String safe(String value) {
        return value == null ? "" : value.trim();
    }

    private boolean isBlank(String value) {
        return value == null || value.isBlank();
    }

    /** 出站消息在 connector 内部使用的最小可信身份。 */
    public record OutboundMessage(
            String clientMessageId,
            String correlationId,
            String platformMessageId,
            Instant registeredAt
    ) {
    }
}
