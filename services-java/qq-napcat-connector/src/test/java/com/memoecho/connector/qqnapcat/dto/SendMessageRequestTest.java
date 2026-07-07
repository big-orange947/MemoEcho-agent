package com.memoecho.connector.qqnapcat.dto;

import org.junit.jupiter.api.Test;

import java.util.List;
import java.util.Map;

import static org.assertj.core.api.Assertions.assertThat;

class SendMessageRequestTest {

    @Test
    void shouldKeepPlainTextMessageCompatible() {
        SendGroupMessageRequest request = new SendGroupMessageRequest(
                138178088L,
                "这是一条测试消息",
                null
        );

        Object payload = request.toNapcatMessage();

        assertThat(payload).isEqualTo("这是一条测试消息");
        assertThat(request.hasValidMessageBody()).isTrue();
    }

    @Test
    void shouldConvertSegmentsToNapcatPayload() {
        SendGroupMessageRequest request = new SendGroupMessageRequest(
                138178088L,
                null,
                List.of(
                        new MessageSegmentPayload("at", Map.of("qq", "3969785168")),
                        new MessageSegmentPayload("text", Map.of("text", " 你好"))
                )
        );

        Object payload = request.toNapcatMessage();

        assertThat(payload).isInstanceOf(List.class);
        @SuppressWarnings("unchecked")
        List<Map<String, Object>> segmentPayload = (List<Map<String, Object>>) payload;
        assertThat(segmentPayload).containsExactly(
                Map.of("type", "at", "data", Map.of("qq", "3969785168")),
                Map.of("type", "text", "data", Map.of("text", " 你好"))
        );
        assertThat(request.hasValidMessageBody()).isTrue();
    }
}
