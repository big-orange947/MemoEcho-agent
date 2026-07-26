package com.memoecho.eventcenter.service;

import com.fasterxml.jackson.databind.ObjectMapper;
import com.memoecho.eventcenter.dto.ConversationMessageResponse;
import com.memoecho.eventcenter.dto.ConversationProgressResponse;
import org.junit.jupiter.api.Test;

import java.util.List;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertFalse;
import static org.junit.jupiter.api.Assertions.assertTrue;
import static org.mockito.BDDMockito.given;
import static org.mockito.Mockito.mock;
import static org.mockito.Mockito.verify;

class ConversationProgressApplicationServiceTest {

    private final ObjectMapper objectMapper = new ObjectMapper();

    @Test
    void shouldRequestRuntimeSummaryOnlyWhenSnapshotIsBuilt() throws Exception {
        // 这个测试函数的作用是验证一次主动快照请求只读取目标用户会话，并返回 Runtime 的自然语言概括。
        EventCenterApplicationService eventCenter = mock(EventCenterApplicationService.class);
        AgentRuntimeDispatchClient runtime = mock(AgentRuntimeDispatchClient.class);
        List<ConversationMessageResponse> messages = List.of(
                message("incoming-1", "对方", "一个月多少钱", "EXTERNAL", "2026-07-13T10:00:00Z"),
                message("agent-1", "我", "一个月十五", "AGENT_AUTO", "2026-07-13T10:01:00Z")
        );
        given(eventCenter.findConversationMessages("user-1", "10001", "qq", "private", 60))
                .willReturn(messages);
        given(runtime.summarizeConversationProgress("user-1", "qq", "private", "10001", messages))
                .willReturn(objectMapper.readTree("""
                        {
                          "summary": "对方正在询问会员价格，我方尚未回复，目前轮到我方继续",
                          "generatedByModel": true,
                          "generatedAt": "2026-07-13T10:02:00Z"
                        }
                        """));
        ConversationProgressApplicationService service = new ConversationProgressApplicationService(eventCenter, runtime);

        ConversationProgressResponse response = service.buildSnapshot("user-1", "qq", "private", "10001", 60, "");

        assertTrue(response.generatedByModel());
        assertEquals("对方正在询问会员价格，我方尚未回复，目前轮到我方继续", response.summary());
        assertEquals(messages, response.messages());
        assertTrue(response.summaryUpdated());
        assertEquals("agent-1", response.latestAgentEventId());
        verify(eventCenter).findConversationMessages("user-1", "10001", "qq", "private", 60);
        verify(runtime).summarizeConversationProgress("user-1", "qq", "private", "10001", messages);
    }

    @Test
    void shouldKeepTimelineAvailableWhenRuntimeIsOffline() {
        // 这个测试函数的作用是验证 Runtime 离线时仍能返回真实时间线和本地概括，而不是让弹窗整体报错。
        EventCenterApplicationService eventCenter = mock(EventCenterApplicationService.class);
        AgentRuntimeDispatchClient runtime = mock(AgentRuntimeDispatchClient.class);
        List<ConversationMessageResponse> messages = List.of(
                message("incoming-1", "对方", "会员还卖吗", "EXTERNAL", "2026-07-13T10:00:00Z"),
                message("outgoing-1", "我", "还卖", "AGENT_AUTO", "2026-07-13T10:01:00Z")
        );
        given(eventCenter.findConversationMessages("user-1", "10001", "qq", "private", 60))
                .willReturn(messages);
        given(runtime.summarizeConversationProgress("user-1", "qq", "private", "10001", messages))
                .willReturn(null);
        ConversationProgressApplicationService service = new ConversationProgressApplicationService(eventCenter, runtime);

        ConversationProgressResponse response = service.buildSnapshot("user-1", "qq", "private", "10001", 60, "");

        assertFalse(response.generatedByModel());
        assertTrue(response.summary().contains("会员还卖吗"));
        assertTrue(response.summary().contains("等对方继续"));
        assertEquals(2, response.messages().size());
    }

    @Test
    void shouldReuseCachedSummaryWhenAgentHasNotRepliedAgain() {
        // 这个测试函数的作用是验证 Agent 游标未变化时只刷新时间线，不再调用 Runtime 消耗模型额度。
        EventCenterApplicationService eventCenter = mock(EventCenterApplicationService.class);
        AgentRuntimeDispatchClient runtime = mock(AgentRuntimeDispatchClient.class);
        List<ConversationMessageResponse> messages = List.of(
                message("incoming-1", "对方", "会员还卖吗", "EXTERNAL", "2026-07-13T10:00:00Z"),
                message("agent-1", "我", "还卖", "AGENT_AUTO", "2026-07-13T10:01:00Z"),
                message("incoming-2", "对方", "一个月吗", "EXTERNAL", "2026-07-13T10:02:00Z")
        );
        given(eventCenter.findConversationMessages("user-1", "10001", "qq", "private", 60))
                .willReturn(messages);
        ConversationProgressApplicationService service = new ConversationProgressApplicationService(eventCenter, runtime);

        ConversationProgressResponse response = service.buildSnapshot(
                "user-1", "qq", "private", "10001", 60, "agent-1"
        );

        assertFalse(response.summaryUpdated());
        assertEquals("", response.summary());
        assertEquals("agent-1", response.latestAgentEventId());
        org.mockito.Mockito.verifyNoInteractions(runtime);
    }

    /** 创建测试时间线消息，未使用字段保持为空，突出消息方向和时间顺序。 */
    private ConversationMessageResponse message(
            String eventId,
            String senderName,
            String text,
            String messageOrigin,
            String timestamp
    ) {
        return new ConversationMessageResponse(
                eventId, "qq", "private", "10001", "测试会话",
                "sender", senderName, messageOrigin.equals("EXTERNAL") ? "member" : "self", null,
                text, timestamp, List.of(), List.of(), true, false,
                "social_reply", "FAST", "COMPLETED", "", "SENT", false,
                "", "OPEN", null, messageOrigin, List.of()
        );
    }
}
