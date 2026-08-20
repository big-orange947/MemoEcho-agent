package com.memoecho.eventcenter.service;

import com.fasterxml.jackson.databind.JsonNode;
import com.fasterxml.jackson.databind.ObjectMapper;
import com.memoecho.eventcenter.dto.ConversationMessageResponse;
import com.memoecho.eventcenter.dto.DispatchResult;
import com.memoecho.eventcenter.dto.DraftConfirmRequest;
import com.memoecho.eventcenter.dto.DraftRejectRequest;
import com.memoecho.eventcenter.dto.QqMessageSendResult;
import com.memoecho.eventcenter.dto.SnoozeEventRequest;
import com.memoecho.eventcenter.dto.SenderPayload;
import com.memoecho.eventcenter.dto.UnifiedEventPayload;
import com.memoecho.eventcenter.model.StoredEvent;
import com.memoecho.eventcenter.repository.EventRecordRepository;
import com.memoecho.eventcenter.repository.InMemoryEventRecordRepository;
import org.junit.jupiter.api.Test;
import org.mockito.ArgumentCaptor;
import org.springframework.http.HttpStatus;

import java.util.List;
import java.time.Instant;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertFalse;
import static org.junit.jupiter.api.Assertions.assertNotNull;
import static org.junit.jupiter.api.Assertions.assertNotEquals;
import static org.junit.jupiter.api.Assertions.assertTrue;
import static org.mockito.BDDMockito.given;
import static org.mockito.Mockito.mock;
import static org.mockito.Mockito.verify;
import static org.mockito.Mockito.when;
import static org.mockito.ArgumentMatchers.any;

class EventCenterApplicationServiceTest {

    private final ObjectMapper objectMapper = new ObjectMapper();

    @Test
    void shouldPropagateResolvedOwnerToRuntimePayload() {
        // 这个测试函数的作用是保证 QQ 账号匹配出的本地用户会随事件传给 Runtime，用于查询同一用户的设定和模型。
        InMemoryEventRecordRepository repository = new InMemoryEventRecordRepository();
        AgentRuntimeDispatchClient dispatchClient = mock(AgentRuntimeDispatchClient.class);
        QqConnectorMessageClient qqConnectorMessageClient = mock(QqConnectorMessageClient.class);
        EventOwnershipResolver ownershipResolver = mock(EventOwnershipResolver.class);
        UnifiedEventPayload event = createEvent("event-owner-propagation");
        given(ownershipResolver.resolveOwnerUserId(event)).willReturn("user-uuid-001");
        given(dispatchClient.dispatch(any(UnifiedEventPayload.class)))
                .willReturn(new DispatchResult(true, 200, objectMapper.createObjectNode(), null));
        EventCenterApplicationService service = new EventCenterApplicationService(
                repository,
                dispatchClient,
                qqConnectorMessageClient,
                new WorkspaceEventStreamService(),
                ownershipResolver
        );

        service.ingest(event);

        ArgumentCaptor<UnifiedEventPayload> captor = ArgumentCaptor.forClass(UnifiedEventPayload.class);
        verify(dispatchClient).dispatch(captor.capture());
        assertEquals("user-uuid-001", captor.getValue().rawPayload().path("userId").asText());
        assertEquals("user-uuid-001", repository.findByEventId(event.eventId()).orElseThrow().ownerUserId());
    }

    @Test
    void shouldArchiveSelfReportedMessageWithoutDispatchingRuntime() {
        // 这个测试函数的作用是验证开启 NapCat 自身消息上报后，自己的消息会进入上下文仓库但绝不触发自动回复循环。
        InMemoryEventRecordRepository repository = new InMemoryEventRecordRepository();
        AgentRuntimeDispatchClient dispatchClient = mock(AgentRuntimeDispatchClient.class);
        QqConnectorMessageClient qqConnectorMessageClient = mock(QqConnectorMessageClient.class);
        EventCenterApplicationService service = new EventCenterApplicationService(repository, dispatchClient, qqConnectorMessageClient);
        UnifiedEventPayload selfEvent = new UnifiedEventPayload(
                "event-self-reported",
                "qq",
                "life",
                "message",
                "private",
                "2597164807",
                "3969785168",
                new SenderPayload("3969785168", "freeze", "member"),
                "我刚才已经说明过了",
                List.of(),
                List.of(),
                "2026-07-13T08:00:00Z",
                objectMapper.createObjectNode()
        );

        service.ingest(selfEvent);

        StoredEvent storedEvent = repository.findByEventId(selfEvent.eventId()).orElseThrow();
        assertEquals("SELF_MESSAGE_RECORDED", storedEvent.processingStatus());
        assertEquals("USER_MANUAL", storedEvent.messageOrigin());
        org.mockito.Mockito.verify(dispatchClient, org.mockito.Mockito.never()).dispatch(any(UnifiedEventPayload.class));
    }

    @Test
    void shouldDispatchDesktopWorkspaceCommandEvenWhenSenderIsCurrentUser() {
        // 这个测试函数的作用是防止主控台命令被误判为“自己发出的聊天消息”，否则前端会显示 Agent Runtime 当前未启用。
        InMemoryEventRecordRepository repository = new InMemoryEventRecordRepository();
        AgentRuntimeDispatchClient dispatchClient = mock(AgentRuntimeDispatchClient.class);
        QqConnectorMessageClient qqConnectorMessageClient = mock(QqConnectorMessageClient.class);
        EventCenterApplicationService service = new EventCenterApplicationService(repository, dispatchClient, qqConnectorMessageClient);
        UnifiedEventPayload desktopCommand = new UnifiedEventPayload(
                "desktop-command-runtime-dispatch",
                "desktop",
                "workspace",
                "desktop_command",
                "private",
                "workspace:user-001",
                "user-001",
                new SenderPayload("user-001", "desktop-user", "owner"),
                "帮我跟km约一下明天晚上打游戏",
                List.of(),
                List.of(),
                "2026-07-26T11:00:00Z",
                objectMapper.createObjectNode()
        );
        given(dispatchClient.dispatch(any(UnifiedEventPayload.class)))
                .willReturn(new DispatchResult(true, 200, objectMapper.createObjectNode(), null));

        var response = service.ingest(desktopCommand);

        assertTrue(response.dispatch().attempted());
        StoredEvent storedEvent = repository.findByEventId(desktopCommand.eventId()).orElseThrow();
        assertNotEquals("SELF_MESSAGE_RECORDED", storedEvent.processingStatus());
        verify(dispatchClient).dispatch(any(UnifiedEventPayload.class));
    }

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
    void shouldExposeSuccessfulAgentReplyAsSelfConversationHistory() throws Exception {
        // 这个测试函数的作用是保证 NapCat 未上报自身消息时，成功发送的 Agent 回复仍会作为“我”进入下一轮上下文。
        InMemoryEventRecordRepository repository = new InMemoryEventRecordRepository();
        AgentRuntimeDispatchClient dispatchClient = mock(AgentRuntimeDispatchClient.class);
        QqConnectorMessageClient qqConnectorMessageClient = mock(QqConnectorMessageClient.class);
        EventOwnershipResolver ownershipResolver = mock(EventOwnershipResolver.class);
        UnifiedEventPayload event = createEvent("event-history-with-agent-reply");
        given(ownershipResolver.resolveOwnerUserId(event)).willReturn("user-uuid-001");
        JsonNode runtimeBody = objectMapper.readTree("""
                {
                  "route": "social_reply",
                  "final_reply": "一个月15",
                  "write_back_actions": ["qq_write_back_sent:message-101"],
                  "results": [{"need_confirmation": false}]
                }
                """);
        given(dispatchClient.dispatch(any(UnifiedEventPayload.class)))
                .willReturn(new DispatchResult(true, 200, runtimeBody, null));
        EventCenterApplicationService service = new EventCenterApplicationService(
                repository,
                dispatchClient,
                qqConnectorMessageClient,
                new WorkspaceEventStreamService(),
                ownershipResolver
        );

        service.ingest(event);

        var messages = service.findConversationMessages(
                "user-uuid-001", event.chatId(), event.platform(), event.chatType(), 10
        );
        assertEquals(2, messages.size());
        assertEquals("3969785168", messages.get(0).senderId());
        assertEquals("一个月15", messages.get(0).text());
        assertEquals("AGENT_AUTO", messages.get(0).messageOrigin());
        assertEquals("2597164807", messages.get(1).senderId());
        assertEquals("EXTERNAL", messages.get(1).messageOrigin());

        // 最新事件是 Agent 代发消息时，会话列表仍应显示真实联系人名称，而不是本人名称或 QQ 号。
        var summaries = service.findConversationSummariesForUser(
                "user-uuid-001", event.platform(), event.chatType(), null, null, null
        );
        assertEquals(1, summaries.size());
        assertEquals("freeze", summaries.get(0).chatName());
    }

    @Test
    void shouldConsumeOnlyOneExplicitSelfMessageForEachMatchingReply() throws Exception {
        // 这个测试函数的作用是防止两次相同回复只上报一次时，去重逻辑错误删除两条合成历史。
        InMemoryEventRecordRepository repository = new InMemoryEventRecordRepository();
        AgentRuntimeDispatchClient dispatchClient = mock(AgentRuntimeDispatchClient.class);
        QqConnectorMessageClient qqConnectorMessageClient = mock(QqConnectorMessageClient.class);
        EventCenterApplicationService service = new EventCenterApplicationService(
                repository, dispatchClient, qqConnectorMessageClient
        );
        JsonNode runtimeBody = objectMapper.readTree("""
                {
                  "route": "social_reply",
                  "final_reply": "还卖",
                  "write_back_actions": ["qq_write_back_sent:message-102"],
                  "results": [{"need_confirmation": false}]
                }
                """);
        given(dispatchClient.dispatch(any(UnifiedEventPayload.class)))
                .willReturn(new DispatchResult(true, 200, runtimeBody, null));

        service.ingest(createEvent("event-same-reply-1"));
        service.ingest(createEvent("event-same-reply-2"));
        UnifiedEventPayload reportedSelfMessage = new UnifiedEventPayload(
                "event-same-reply-self",
                "qq",
                "social",
                "message",
                "private",
                "2597164807",
                "3969785168",
                new SenderPayload("3969785168", "freeze", "member"),
                "还卖",
                List.of(),
                List.of(),
                "2026-07-13T08:00:03Z",
                objectMapper.createObjectNode()
        );
        service.ingest(reportedSelfMessage);

        var messages = service.findConversationMessages("2597164807", "qq", "private", 20);
        long replyCount = messages.stream().filter(message -> "还卖".equals(message.text())).count();

        assertEquals(4, messages.size());
        assertEquals(2, replyCount);
    }

    @Test
    void shouldPreferExplicitAgentCorrelationOverSyntheticReply() throws Exception {
        // 这个测试函数的作用是保证真实 Webhook 回流会替换合成回复，避免同一条 Agent 消息重复进入上下文。
        InMemoryEventRecordRepository repository = new InMemoryEventRecordRepository();
        AgentRuntimeDispatchClient dispatchClient = mock(AgentRuntimeDispatchClient.class);
        QqConnectorMessageClient qqConnectorMessageClient = mock(QqConnectorMessageClient.class);
        EventCenterApplicationService service = new EventCenterApplicationService(
                repository, dispatchClient, qqConnectorMessageClient
        );
        UnifiedEventPayload incoming = createEvent("event-explicit-correlation");
        JsonNode runtimeBody = objectMapper.readTree("""
                {
                  "route": "social_reply",
                  "final_reply": "一个月15",
                  "write_back_actions": ["qq_write_back_sent:message-90001"],
                  "results": [{"need_confirmation": false}]
                }
                """);
        given(dispatchClient.dispatch(any(UnifiedEventPayload.class)))
                .willReturn(new DispatchResult(true, 200, runtimeBody, null));

        service.ingest(incoming);
        UnifiedEventPayload explicitAgentReply = new UnifiedEventPayload(
                "qq:message_sent:private:90001",
                "qq",
                "social",
                "message_sent",
                "private",
                incoming.chatId(),
                incoming.selfId(),
                new SenderPayload(incoming.selfId(), "哈吉仙", "member"),
                "一个月15",
                List.of(),
                List.of(),
                "2026-07-10T08:00:02Z",
                objectMapper.createObjectNode(),
                "AGENT",
                "90001",
                "client-90001",
                incoming.eventId(),
                90001L
        );
        service.ingest(explicitAgentReply);

        var messages = service.findConversationMessages(incoming.chatId(), "qq", "private", 20);
        var agentMessages = messages.stream()
                .filter(message -> "AGENT".equals(message.actorType()))
                .toList();

        assertEquals(2, messages.size());
        assertEquals(1, agentMessages.size());
        assertEquals(explicitAgentReply.eventId(), agentMessages.get(0).eventId());
        assertEquals(incoming.eventId(), agentMessages.get(0).correlationId());
        assertEquals("AGENT_AUTO", agentMessages.get(0).messageOrigin());
    }

    @Test
    void shouldReadLegacyLocalUserHistoryThroughCurrentPlatformOwner() {
        // 这个测试函数的作用是兼容连接建立前写入 local-user 的历史，同时仍通过平台账号归属避免跨用户读取。
        InMemoryEventRecordRepository repository = new InMemoryEventRecordRepository();
        AgentRuntimeDispatchClient dispatchClient = mock(AgentRuntimeDispatchClient.class);
        QqConnectorMessageClient qqConnectorMessageClient = mock(QqConnectorMessageClient.class);
        EventOwnershipResolver ownershipResolver = mock(EventOwnershipResolver.class);
        UnifiedEventPayload event = createEvent("event-legacy-owner");
        repository.save(StoredEvent.received(event.eventId(), event, Instant.parse("2026-07-10T08:00:00Z")));
        given(ownershipResolver.isConnectedAccountOwnedBy("user-uuid-001", event)).willReturn(true);
        EventCenterApplicationService service = new EventCenterApplicationService(
                repository,
                dispatchClient,
                qqConnectorMessageClient,
                new WorkspaceEventStreamService(),
                ownershipResolver
        );

        var messages = service.findConversationMessages(
                "user-uuid-001", event.chatId(), event.platform(), event.chatType(), 10
        );

        assertEquals(1, messages.size());
        assertEquals(event.eventId(), messages.get(0).eventId());
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
        // 这个测试函数的作用是验证执行轨迹仅保存 Agent、工具名称、记忆 ID 和状态，不会保存 API Key、系统提示词或工具参数。
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
                  "verified_memory_ids": ["memory-001", "memory-001", "", "memory-002", {"text": "不得保存"}],
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
        assertEquals(List.of("memory-001", "memory-002"), storedEvent.executionTrace().verifiedMemoryIds());
        assertNotNull(storedEvent.executionTrace().notification());
        assertEquals("HIGH", storedEvent.executionTrace().notification().priority());
        assertEquals("at_self", storedEvent.executionTrace().notification().triggerReason());
        assertTrue(storedEvent.executionTrace().notification().notifyNow());
        assertFalse(storedEvent.executionTrace().toString().contains("super-secret-key"));
        assertFalse(storedEvent.executionTrace().toString().contains("不要暴露这个提示词"));
    }

    @Test
    void shouldReturnOwnedConversationContextAroundSourceEvent() {
        // 这个测试验证日程来源上下文只截取同一会话，并保留来源消息前后的真实聊天顺序。
        InMemoryEventRecordRepository repository = new InMemoryEventRecordRepository();
        AgentRuntimeDispatchClient dispatchClient = mock(AgentRuntimeDispatchClient.class);
        QqConnectorMessageClient qqConnectorMessageClient = mock(QqConnectorMessageClient.class);
        EventCenterApplicationService service = new EventCenterApplicationService(
                repository, dispatchClient, qqConnectorMessageClient
        );
        UnifiedEventPayload first = createEvent("context-event-1");
        UnifiedEventPayload source = createEvent("context-event-source");
        UnifiedEventPayload third = createEvent("context-event-3");
        repository.save(StoredEvent.received(first.eventId(), "local-user", first, Instant.parse("2026-07-10T08:00:00Z")));
        repository.save(StoredEvent.received(source.eventId(), "local-user", source, Instant.parse("2026-07-10T08:01:00Z")));
        repository.save(StoredEvent.received(third.eventId(), "local-user", third, Instant.parse("2026-07-10T08:02:00Z")));

        var messages = service.findConversationContextAroundEvent("local-user", source.eventId(), 1);

        assertEquals(List.of("context-event-1", "context-event-source", "context-event-3"),
                messages.stream().map(ConversationMessageResponse::eventId).toList());
        assertEquals(source.eventId(), service.findOwnedSourceMessage("local-user", source.eventId())
                .orElseThrow().eventId());
    }

    /**
     * 历史查询失败时必须显式暴露 502 并携带完整诊断，
     * 而不是静默返回空列表掩盖事件表问题。
     */
    @Test
    void shouldExposeHistoryQueryFailureInsteadOfSilentlyEmptyList() {
        EventRecordRepository repository = mock(EventRecordRepository.class);
        AgentRuntimeDispatchClient dispatchClient = mock(AgentRuntimeDispatchClient.class);
        QqConnectorMessageClient qqConnectorMessageClient = mock(QqConnectorMessageClient.class);
        when(repository.findAll()).thenThrow(new IllegalStateException("json damaged"));
        EventCenterApplicationService service = new EventCenterApplicationService(
                repository, dispatchClient, qqConnectorMessageClient);

        org.springframework.web.server.ResponseStatusException exception = org.junit.jupiter.api.Assertions.assertThrows(
                org.springframework.web.server.ResponseStatusException.class,
                () -> service.findConversationMessages("user-1", "10001", "qq", "private", 50, null, null));

        assertEquals(HttpStatus.BAD_GATEWAY, exception.getStatusCode());
        assertTrue(exception.getReason().contains("10001"));
        verify(repository).findAll();
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
