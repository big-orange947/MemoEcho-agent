package com.memoecho.connector.qqnapcat.service;

import com.fasterxml.jackson.databind.JsonNode;
import com.memoecho.connector.qqnapcat.dto.NapcatApiResponse;
import org.springframework.stereotype.Component;

import java.time.Duration;
import java.time.Instant;
import java.util.Map;
import java.util.concurrent.CompletableFuture;
import java.util.concurrent.CompletionException;
import java.util.concurrent.ConcurrentHashMap;
import java.util.function.Supplier;

/**
 * 对最终发往 NapCat 的请求执行幂等保护。
 *
 * <p>Python Runtime 会为一次逻辑发送生成稳定的 clientMessageId。相同 ID 可能因并发事件、
 * HTTP 重试或服务恢复而多次到达 Java 连接器，本组件保证它们只产生一次真实平台调用。</p>
 */
@Component
public class OutboundRequestDeduplicator {

    private static final Duration RETENTION = Duration.ofHours(1);
    private static final int MAX_ENTRIES = 4096;

    private final Map<String, Entry> entries = new ConcurrentHashMap<>();

    /**
     * 执行一次具有稳定请求 ID 的发送，并让重复调用复用第一次发送的响应。
     * 发送异常不会被缓存，调用方稍后仍可使用相同 ID 重试。
     */
    public NapcatApiResponse<JsonNode> execute(
            String clientMessageId,
            Supplier<NapcatApiResponse<JsonNode>> sender
    ) {
        if (clientMessageId == null || clientMessageId.isBlank()) {
            return sender.get();
        }

        cleanupIfNeeded();
        String key = clientMessageId.trim();
        while (true) {
            Entry existing = entries.get(key);
            if (existing != null && !existing.expired()) {
                return await(existing.response());
            }
            if (existing != null) {
                entries.remove(key, existing);
            }

            CompletableFuture<NapcatApiResponse<JsonNode>> response = new CompletableFuture<>();
            Entry created = new Entry(response, Instant.now());
            Entry raced = entries.putIfAbsent(key, created);
            if (raced != null) {
                continue;
            }

            try {
                NapcatApiResponse<JsonNode> result = sender.get();
                response.complete(result);
                return result;
            } catch (RuntimeException exception) {
                entries.remove(key, created);
                response.completeExceptionally(exception);
                throw exception;
            }
        }
    }

    /** 等待首个发送者完成，并保留原始运行时异常。 */
    private NapcatApiResponse<JsonNode> await(CompletableFuture<NapcatApiResponse<JsonNode>> response) {
        try {
            return response.join();
        } catch (CompletionException exception) {
            if (exception.getCause() instanceof RuntimeException runtimeException) {
                throw runtimeException;
            }
            throw exception;
        }
    }

    /** 在缓存过期或超过上限时清理旧请求，避免常驻连接器无限占用内存。 */
    private void cleanupIfNeeded() {
        if (entries.size() < MAX_ENTRIES) {
            return;
        }
        entries.entrySet().removeIf(item -> item.getValue().expired());
        if (entries.size() < MAX_ENTRIES) {
            return;
        }
        entries.entrySet().stream()
                .sorted(Map.Entry.comparingByValue((left, right) -> left.createdAt().compareTo(right.createdAt())))
                .limit(Math.max(1, entries.size() - MAX_ENTRIES + 1L))
                .map(Map.Entry::getKey)
                .toList()
                .forEach(entries::remove);
    }

    private record Entry(
            CompletableFuture<NapcatApiResponse<JsonNode>> response,
            Instant createdAt
    ) {
        private boolean expired() {
            return createdAt.plus(RETENTION).isBefore(Instant.now());
        }
    }
}
