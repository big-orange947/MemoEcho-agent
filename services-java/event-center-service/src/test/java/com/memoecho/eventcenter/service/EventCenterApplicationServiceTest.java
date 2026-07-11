package com.memoecho.eventcenter.service;

import com.fasterxml.jackson.databind.JsonNode;
import com.fasterxml.jackson.databind.ObjectMapper;
import com.memoecho.eventcenter.dto.DispatchResult;
import com.memoecho.eventcenter.dto.DraftConfirmRequest;
import com.memoecho.eventcenter.dto.DraftRejectRequest;
import com.memoecho.eventcenter.dto.QqMessageSendResult;
import com.memoecho.eventcenter.dto.SnoozeEventRequest;
import com.memoecho.eventcenter.dto.SenderPayload;
import com.memoecho.eventcenter.dto.UnifiedEventPayload;
import com.memoecho.eventcenter.model.StoredEvent;
import com.memoecho.eventcenter.repository.InMemoryEventRecordRepository;
import org.junit.jupiter.api.Test;

import java.util.List;
import java.time.Instant;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertFalse;
import static org.junit.jupiter.api.Assertions.assertNotNull;
import static org.junit.jupiter.api.Assertions.assertTrue;
import static org.mockito.BDDMockito.given;
import static org.mockito.Mockito.mock;
import static org.mockito.Mockito.verify;

class EventCenterApplicationServiceTest {

    private final ObjectMapper objectMapper = new ObjectMapper();

    @Test
    void shouldPersistConfirmationRequiredStatusReturnedByRuntime() throws Exception {
        // 这个测试函数的作用是验证 runtime 要求人工确认时，事件中心会保存待确认状态并标记工作台需要处理。
        InMemoryEventRecordRepository repository = new InMemoryEventRecordRepository();
        AgentRuntimeDispatchClient dispatchClient = mock(AgentRuntimeDispatchClient.class);
        QqConnectorMessageClient qqConnectorMessageClient = mock(QqConnectorMessageClient.class);
        EventCenterApplicationService service = new EventCenterApplicationService(repository, dispatchClient, qqConnectorMessageClient);
        UnifiedEventPayload event = createEvent("event-confirm-required");
        JsonNode runtimeBody = objectMapper.readTree("""
                {
                  "route": "social_reply",
                  "summary": "回复草稿已经生成",
                  "final_reply": "你好，我稍后回复你。",
                  "write_back_actions": ["qq_write_back_skipped:confirm_required"],
                  "results": [{"need_confirmation": true}]
                }
                """);
        given(dispatchClient.dispatch(event)).willReturn(new DispatchResult(true, 200, runtimeBody, null));

        service.ingest(event);

        StoredEvent storedEvent = repository.findByEventId(event.eventId()).orElseThrow();
        assertEquals("NEEDS_CONFIRMATION", storedEvent.processingStatus());
        assertEquals("CONFIRM_REQUIRED", storedEvent.writeBackStatus());
        assertEquals("social_reply", storedEvent.resolvedRoute());
        assertTrue(storedEvent.needHumanConfirmation());
        assertNotNull(storedEvent.processedAt());
        assertEquals("你好，我稍后回复你。", storedEvent.replyDraft());
    }

    @Test
    void shouldPersistAutoRepliedStatusWhenWriteBackSucceeded() throws Exception {
        // 这个测试函数的作用是验证 runtime 已经成功回写 QQ 时，事件中心会把消息标记为自动回复完成。
        InMemoryEventRecordRepository repository = new InMemoryEventRecordRepository();
        AgentRuntimeDispatchClient dispatchClient = mock(AgentRuntimeDispatchClient.class);
        QqConnectorMessageClient qqConnectorMessageClient = mock(QqConnectorMessageClient.class);
        EventCenterApplicationService service = new EventCenterApplicationService(repository, dispatchClient, qqConnectorMessageClient);
        UnifiedEventPayload event = createEvent("event-auto-replied");
        JsonNode runtimeBody = objectMapper.readTree("""
                {
                  "route": "social_reply",
                  "summary": "消息已经自动回复",
                  "write_back_actions": ["qq_write_back_sent:message-100"],
                  "results": [{"need_confirmation": false}]
                }
                """);
        given(dispatchClient.dispatch(event)).willReturn(new DispatchResult(true, 200, runtimeBody, null));

        service.ingest(event);

        StoredEvent storedEvent = repository.findByEventId(event.eventId()).orElseThrow();
        assertEquals("AUTO_REPLIED", storedEvent.processingStatus());
        assertEquals("SENT", storedEvent.writeBackStatus());
        assertFalse(storedEvent.needHumanConfirmation());
    }

    @Test
    void shouldSendEditedDraftAfterUserConfirmation() throws Exception {
        // 这个测试函数的作用是验证用户确认时可以覆盖 Runtime 草稿，并且只发送到该事件对应的原始会话。
        InMemoryEventRecordRepository repository = new InMemoryEventRecordRepository();
        AgentRuntimeDispatchClient dispatchClient = mock(AgentRuntimeDispatchClient.class);
        QqConnectorMessageClient qqConnectorMessageClient = mock(QqConnectorMessageClient.class);
        EventCenterApplicationService service = new EventCenterApplicationService(repository, dispatchClient, qqConnectorMessageClient);
        UnifiedEventPayload event = createEvent("event-confirm-send");
        JsonNode runtimeBody = objectMapper.readTree("""
                {
                  "route": "social_reply",
                  "final_reply": "原始草稿",
                  "write_back_actions": ["qq_write_back_skipped:confirm_required"],
                  "results": [{"need_confirmation": true}]
                }
                """);
        given(dispatchClient.dispatch(event)).willReturn(new DispatchResult(true, 200, runtimeBody, null));
        given(qqConnectorMessageClient.sendText(event, "用户编辑后的草稿"))
                .willReturn(new QqMessageSendResult(true, "message_id=100"));

        service.ingest(event);
        service.confirmDraft(event.eventId(), new DraftConfirmRequest("用户编辑后的草稿", "确认后发送"));

        StoredEvent storedEvent = repository.findByEventId(event.eventId()).orElseThrow();
        assertEquals("MANUALLY_SENT", storedEvent.processingStatus());
        assertEquals("SENT", storedEvent.writeBackStatus());
        assertEquals("CONFIRMED", storedEvent.lastAction());
        assertEquals("用户编辑后的草稿", storedEvent.replyDraft());
        assertFalse(storedEvent.needHumanConfirmation());
        verify(qqConnectorMessageClient).sendText(event, "用户编辑后的草稿");
    }

    @Test
    void shouldRejectDraftWithoutSendingExternalMessage() throws Exception {
        // 这个测试函数的作用是验证拒绝草稿只更新事件状态和审计原因，不会调用 QQ Connector。
        InMemoryEventRecordRepository repository = new InMemoryEventRecordRepository();
        AgentRuntimeDispatchClient dispatchClient = mock(AgentRuntimeDispatchClient.class);
        QqConnectorMessageClient qqConnectorMessageClient = mock(QqConnectorMessageClient.class);
        EventCenterApplicationService service = new EventCenterApplicationService(repository, dispatchClient, qqConnectorMessageClient);
        UnifiedEventPayload event = createEvent("event-reject-draft");
        JsonNode runtimeBody = objectMapper.readTree("""
                {
                  "route": "social_reply",
                  "final_reply": "等待确认的草稿",
                  "write_back_actions": ["qq_write_back_skipped:draft_only"],
                  "results": []
                }
                """);
        given(dispatchClient.dispatch(event)).willReturn(new DispatchResult(true, 200, runtimeBody, null));

        service.ingest(event);
        service.rejectDraft(event.eventId(), new DraftRejectRequest("语气不合适"));

        StoredEvent storedEvent = repository.findByEventId(event.eventId()).orElseThrow();
        assertEquals("DRAFT_REJECTED", storedEvent.processingStatus());
        assertEquals("REJECTED", storedEvent.writeBackStatus());
        assertEquals("REJECTED", storedEvent.lastAction());
        assertEquals("语气不合适", storedEvent.lastActionNote());
        verify(qqConnectorMessageClient, org.mockito.Mockito.never()).sendText(event, "等待确认的草稿");
    }

    @Test
    void shouldRetryFailedDispatchAndRefreshDraft() throws Exception {
        // 这个测试函数的作用是验证 Runtime 派发失败后可重新执行，并用新的处理结果和草稿替换旧失败状态。
        InMemoryEventRecordRepository repository = new InMemoryEventRecordRepository();
        AgentRuntimeDispatchClient dispatchClient = mock(AgentRuntimeDispatchClient.class);
        QqConnectorMessageClient qqConnectorMessageClient = mock(QqConnectorMessageClient.class);
        EventCenterApplicationService service = new EventCenterApplicationService(repository, dispatchClient, qqConnectorMessageClient);
        UnifiedEventPayload event = createEvent("event-retry-dispatch");
        JsonNode retryBody = objectMapper.readTree("""
                {
                  "route": "social_reply",
                  "final_reply": "重试后生成的新草稿",
                  "write_back_actions": ["qq_write_back_skipped:confirm_required"],
                  "results": [{"need_confirmation": true}]
                }
                """);
        given(dispatchClient.dispatch(event)).willReturn(
                new DispatchResult(true, null, null, "runtime timeout"),
                new DispatchResult(true, 200, retryBody, null)
        );

        service.ingest(event);
        service.retryEvent(event.eventId());

        StoredEvent storedEvent = repository.findByEventId(event.eventId()).orElseThrow();
        assertEquals("NEEDS_CONFIRMATION", storedEvent.processingStatus());
        assertEquals("RETRIED", storedEvent.lastAction());
        assertEquals("重试后生成的新草稿", storedEvent.replyDraft());
        assertTrue(storedEvent.needHumanConfirmation());
    }

    @Test
    void shouldManageInboxLifecycleAndExcludeCompletedEventsFromConversationList() {
        // 这个测试函数的作用是验证消息从新消息到已读、稍后处理、已完成的状态流转，并确认已完成消息不再进入工作台会话摘要。
        InMemoryEventRecordRepository repository = new InMemoryEventRecordRepository();
        AgentRuntimeDispatchClient dispatchClient = mock(AgentRuntimeDispatchClient.class);
        QqConnectorMessageClient qqConnectorMessageClient = mock(QqConnectorMessageClient.class);
        EventCenterApplicationService service = new EventCenterApplicationService(repository, dispatchClient, qqConnectorMessageClient);
        UnifiedEventPayload event = createEvent("event-inbox-lifecycle");
        given(dispatchClient.dispatch(event)).willReturn(new DispatchResult(true, 200, objectMapper.createObjectNode(), null));

        service.ingest(event);
        assertEquals(1, service.findAll("NEW").size());

        assertEquals("READ", service.markInboxRead(event.eventId()).inboxStatus());
        assertEquals(1, service.findAll("READ").size());

        Instant snoozedUntil = Instant.now().plusSeconds(600);
        assertEquals("SNOOZED", service.snoozeInboxEvent(event.eventId(), new SnoozeEventRequest(snoozedUntil)).inboxStatus());
        assertEquals(1, service.findAll("SNOOZED").size());
        assertTrue(service.findConversationSummaries(null, null, null, null, null).isEmpty());

        assertEquals("DONE", service.markInboxDone(event.eventId()).inboxStatus());
        assertTrue(service.findConversationSummaries(null, null, null, null, null).isEmpty());
        assertEquals(1, service.findAll("DONE").size());
    }

    @Test
    void shouldExcludeIgnoredConfirmationDraftFromActionRequiredConversations() throws Exception {
        // 这个测试函数的作用是验证用户忽略待确认草稿后，工作台不会继续将它展示为待处理重点消息。
        InMemoryEventRecordRepository repository = new InMemoryEventRecordRepository();
        AgentRuntimeDispatchClient dispatchClient = mock(AgentRuntimeDispatchClient.class);
        QqConnectorMessageClient qqConnectorMessageClient = mock(QqConnectorMessageClient.class);
        EventCenterApplicationService service = new EventCenterApplicationService(repository, dispatchClient, qqConnectorMessageClient);
        UnifiedEventPayload event = createEvent("event-ignore-confirmation");
        JsonNode runtimeBody = objectMapper.readTree("""
                {
                  "route": "social_reply",
                  "final_reply": "等待确认的回复草稿",
                  "write_back_actions": ["qq_write_back_skipped:confirm_required"],
                  "results": [{"need_confirmation": true}]
                }
                """);
        given(dispatchClient.dispatch(event)).willReturn(new DispatchResult(true, 200, runtimeBody, null));

        service.ingest(event);
        assertEquals(1, service.findConversationSummaries(null, null, null, null, null).size());

        assertEquals("IGNORED", service.ignoreInboxEvent(event.eventId()).inboxStatus());
        assertTrue(service.findConversationSummaries(null, null, null, null, null).isEmpty());
    }

    @Test
    void shouldPersistSanitizedExecutionTraceWithoutSecretsOrToolArguments() throws Exception {
        // 这个测试函数的作用是验证执行轨迹仅保存 Agent、工具名称和状态，不会保存 API Key、系统提示词或工具参数。
        InMemoryEventRecordRepository repository = new InMemoryEventRecordRepository();
        AgentRuntimeDispatchClient dispatchClient = mock(AgentRuntimeDispatchClient.class);
        QqConnectorMessageClient qqConnectorMessageClient = mock(QqConnectorMessageClient.class);
        EventCenterApplicationService service = new EventCenterApplicationService(repository, dispatchClient, qqConnectorMessageClient);
        UnifiedEventPayload event = createEvent("event-execution-trace");
        JsonNode runtimeBody = objectMapper.readTree("""
                {
                  "execution_id": "execution-100",
                  "route": "social_reply",
                  "summary": "社交回复草稿已生成",
                  "write_back_actions": ["qq_write_back_failed:apiKey=super-secret-key"],
                  "notification": {
                    "channel": "urgent",
                    "priority": "HIGH",
                    "trigger_reason": "at_self",
                    "notify_now": true,
                    "aggregation_key": "qq:group:1098307542",
                    "aggregation_status": "IMMEDIATE",
                    "buffered_count": 0,
                    "summary_candidate": ""
                  },
                  "results": [
                    {
                      "agent": "social",
                      "status": "success",
                      "tool_calls": [
                        {"tool": "send_qq_message", "arguments": {"apiKey": "super-secret-key", "message": "private text"}}
                      ],
                      "next_actions": ["await_user_confirmation"],
                      "need_confirmation": true,
                      "structured_result": {
                        "effectiveSystemPrompt": "不要暴露这个提示词",
                        "resolvedModelProfile": {"apiKey": "super-secret-key"}
                      }
                    }
                  ]
                }
                """);
        given(dispatchClient.dispatch(event)).willReturn(new DispatchResult(true, 200, runtimeBody, null));

        service.ingest(event);

        StoredEvent storedEvent = repository.findByEventId(event.eventId()).orElseThrow();
        assertNotNull(storedEvent.executionTrace());
        assertEquals("execution-100", storedEvent.executionTrace().executionId());
        assertEquals(List.of("qq_write_back_failed"), storedEvent.executionTrace().writeBackActions());
        assertEquals(List.of("send_qq_message"), storedEvent.executionTrace().steps().get(0).toolNames());
        assertNotNull(storedEvent.executionTrace().notification());
        assertEquals("HIGH", storedEvent.executionTrace().notification().priority());
        assertEquals("at_self", storedEvent.executionTrace().notification().triggerReason());
        assertTrue(storedEvent.executionTrace().notification().notifyNow());
        assertFalse(storedEvent.executionTrace().toString().contains("super-secret-key"));
        assertFalse(storedEvent.executionTrace().toString().contains("不要暴露这个提示词"));
    }

    private UnifiedEventPayload createEvent(String eventId) {
        // 这个测试辅助函数的作用是创建一条结构完整、可重复复用的 QQ 私聊标准事件。
        return new UnifiedEventPayload(
                eventId,
                "qq",
                "life",
                "message",
                "private",
                "2597164807",
                "3969785168",
                new SenderPayload("2597164807", "freeze", "member"),
                "今天下午的会议我能参加。",
                List.of(),
                List.of(),
                "2026-07-10T08:00:00Z",
                objectMapper.createObjectNode()
        );
    }
}
