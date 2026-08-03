package com.memoecho.connector.qqnapcat.service;

import com.fasterxml.jackson.databind.JsonNode;
import com.fasterxml.jackson.databind.node.JsonNodeFactory;
import com.memoecho.connector.qqnapcat.dto.NapcatApiResponse;
import org.junit.jupiter.api.Test;

import java.util.concurrent.CompletableFuture;
import java.util.concurrent.CountDownLatch;
import java.util.concurrent.atomic.AtomicInteger;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertSame;

class OutboundRequestDeduplicatorTest {

    @Test
    void shouldReuseResponseForSameClientMessageId() {
        // 同一个逻辑发送即使被重复提交，也只能调用一次真实发送函数。
        OutboundRequestDeduplicator deduplicator = new OutboundRequestDeduplicator();
        AtomicInteger calls = new AtomicInteger();
        NapcatApiResponse<JsonNode> expected = successfulResponse(1001L);

        NapcatApiResponse<JsonNode> first = deduplicator.execute(
                "task-1:turn-1:content-a",
                () -> {
                    calls.incrementAndGet();
                    return expected;
                }
        );
        NapcatApiResponse<JsonNode> second = deduplicator.execute(
                "task-1:turn-1:content-a",
                () -> {
                    calls.incrementAndGet();
                    return successfulResponse(1002L);
                }
        );

        assertEquals(1, calls.get());
        assertSame(first, second);
    }

    @Test
    void shouldSerializeConcurrentRequestsWithSameClientMessageId() {
        // 并发进入连接器的相同请求必须等待首个发送完成，不能同时穿透到 NapCat。
        OutboundRequestDeduplicator deduplicator = new OutboundRequestDeduplicator();
        AtomicInteger calls = new AtomicInteger();
        CountDownLatch senderStarted = new CountDownLatch(1);
        CountDownLatch releaseSender = new CountDownLatch(1);

        CompletableFuture<NapcatApiResponse<JsonNode>> first = CompletableFuture.supplyAsync(
                () -> deduplicator.execute("same-request", () -> {
                    calls.incrementAndGet();
                    senderStarted.countDown();
                    await(releaseSender);
                    return successfulResponse(2001L);
                })
        );
        await(senderStarted);
        CompletableFuture<NapcatApiResponse<JsonNode>> second = CompletableFuture.supplyAsync(
                () -> deduplicator.execute("same-request", () -> {
                    calls.incrementAndGet();
                    return successfulResponse(2002L);
                })
        );
        releaseSender.countDown();

        assertEquals("2001", first.join().data().path("message_id").asText());
        assertEquals("2001", second.join().data().path("message_id").asText());
        assertEquals(1, calls.get());
    }

    @Test
    void shouldAllowDifferentClientMessageIds() {
        // 不同请求 ID 代表 Agent 的不同主动消息，不能被误判为重复发送。
        OutboundRequestDeduplicator deduplicator = new OutboundRequestDeduplicator();
        AtomicInteger calls = new AtomicInteger();

        deduplicator.execute("request-a", () -> {
            calls.incrementAndGet();
            return successfulResponse(3001L);
        });
        deduplicator.execute("request-b", () -> {
            calls.incrementAndGet();
            return successfulResponse(3002L);
        });

        assertEquals(2, calls.get());
    }

    private static NapcatApiResponse<JsonNode> successfulResponse(long messageId) {
        return new NapcatApiResponse<>(
                "ok",
                0,
                JsonNodeFactory.instance.objectNode().put("message_id", messageId),
                "",
                "",
                ""
        );
    }

    private static void await(CountDownLatch latch) {
        try {
            latch.await();
        } catch (InterruptedException exception) {
            Thread.currentThread().interrupt();
            throw new IllegalStateException(exception);
        }
    }
}
