package com.memoecho.eventcenter.service;

import com.fasterxml.jackson.databind.ObjectMapper;
import com.memoecho.eventcenter.dto.ConversationCognitionCardResponse;
import com.memoecho.eventcenter.dto.ConversationCognitionCardUpsertRequest;
import com.memoecho.eventcenter.dto.ConversationMessageResponse;
import com.memoecho.eventcenter.model.ConversationCognitionCard;
import org.junit.jupiter.api.Test;
import org.mockito.ArgumentCaptor;

import java.time.Instant;
import java.util.List;
import java.util.Optional;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertNull;
import static org.mockito.BDDMockito.given;
import static org.mockito.Mockito.mock;
import static org.mockito.Mockito.verify;
import static org.mockito.Mockito.verifyNoInteractions;

class ConversationCognitionRefreshServiceTest {

    private final ObjectMapper objectMapper = new ObjectMapper();

    @Test
    void shouldSkipRuntimeWhenSourceEventsHaveNotChanged() {
        // 这个测试函数的作用是验证用户重复打开同一认知卡时不会再次消耗模型额度。
        EventCenterApplicationService eventCenter = mock(EventCenterApplicationService.class);
        AgentRuntimeDispatchClient runtime = mock(AgentRuntimeDispatchClient.class);
        ConversationCognitionCardApplicationService cards = mock(ConversationCognitionCardApplicationService.class);
        List<ConversationMessageResponse> messages = List.of(message("event-1", "你好"));
        ConversationCognitionCardResponse existing = response(List.of("event-1"));
        given(eventCenter.findConversationMessages("user-1", "10001", "qq", "private", 80))
                .willReturn(messages);
        given(cards.find("user-1", "qq", "private", "10001")).willReturn(Optional.of(existing));
        ConversationCognitionRefreshService service = new ConversationCognitionRefreshService(eventCenter, runtime, cards);

        ConversationCognitionCardResponse result = service.refresh(
                "user-1", "qq", "private", "10001", 80);

        assertEquals(existing, result);
        verifyNoInteractions(runtime);
    }

    @Test
    void shouldUseAuthoritativeEventIdsAndPreserveFieldsOnFallback() throws Exception {
        // 这个测试函数的作用是验证 Runtime 降级结果只更新当前进度，并由 Java 注入真实事件来源。
        EventCenterApplicationService eventCenter = mock(EventCenterApplicationService.class);
        AgentRuntimeDispatchClient runtime = mock(AgentRuntimeDispatchClient.class);
        ConversationCognitionCardApplicationService cards = mock(ConversationCognitionCardApplicationService.class);
        List<ConversationMessageResponse> messages = List.of(
                message("event-1", "你好"),
                message("event-2", "明天下午见")
        );
        given(eventCenter.findConversationMessages("user-1", "10001", "qq", "private", 80))
                .willReturn(messages);
        given(cards.find("user-1", "qq", "private", "10001")).willReturn(Optional.of(response(List.of("event-1"))));
        given(runtime.analyzeConversationCognition("user-1", "qq", "private", "10001", messages))
                .willReturn(objectMapper.readTree("""
                        {
                          "currentProgress": {"value": "对方已发送最新消息", "confidence": 1.0},
                          "sourceEventIds": ["forged-id"],
                          "sourceMessageCount": 999,
                          "generatedByModel": false
                        }
                        """));
        given(cards.upsertInference(org.mockito.ArgumentMatchers.eq("user-1"), org.mockito.ArgumentMatchers.any()))
                .willReturn(response(List.of("event-1", "event-2")));
        ConversationCognitionRefreshService service = new ConversationCognitionRefreshService(eventCenter, runtime, cards);

        service.refresh("user-1", "qq", "private", "10001", 80);

        ArgumentCaptor<ConversationCognitionCardUpsertRequest> captor =
                ArgumentCaptor.forClass(ConversationCognitionCardUpsertRequest.class);
        verify(cards).upsertInference(org.mockito.ArgumentMatchers.eq("user-1"), captor.capture());
        ConversationCognitionCardUpsertRequest request = captor.getValue();
        assertEquals(List.of("event-1", "event-2"), request.sourceEventIds());
        assertEquals(2, request.sourceMessageCount());
        assertEquals("对方已发送最新消息", request.currentProgress().value());
        assertNull(request.relationship());
        assertNull(request.knownFacts());
    }

    /** 创建刷新链路使用的最小消息，未使用字段保持为空。 */
    private ConversationMessageResponse message(String eventId, String text) {
        return new ConversationMessageResponse(
                eventId, "qq", "private", "10001", "测试会话",
                "peer", "对方", "member", null, text,
                "2026-07-20T10:00:00Z", List.of(), List.of(), true, false,
                "social_reply", "FAST", "COMPLETED", "", "", false,
                "", "OPEN", null, "EXTERNAL", List.of()
        );
    }

    /** 创建带来源游标的认知卡响应，便于验证增量去重。 */
    private ConversationCognitionCardResponse response(List<String> sourceEventIds) {
        ConversationCognitionCard.CognitionField empty = ConversationCognitionCard.CognitionField.empty();
        Instant now = Instant.parse("2026-07-20T10:00:00Z");
        return new ConversationCognitionCardResponse(
                "card-1", "qq", "private", "10001", 1,
                empty, empty, empty, empty, empty, empty, empty,
                List.of(), List.of(), List.of(), sourceEventIds, sourceEventIds.size(),
                "INFERRED", now, now, now
        );
    }
}
