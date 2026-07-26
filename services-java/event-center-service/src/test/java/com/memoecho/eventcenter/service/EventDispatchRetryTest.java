package com.memoecho.eventcenter.service;

import com.fasterxml.jackson.databind.ObjectMapper;
import com.memoecho.eventcenter.config.AgentDispatchRetryProperties;
import com.memoecho.eventcenter.dto.DispatchResult;
import com.memoecho.eventcenter.dto.SenderPayload;
import com.memoecho.eventcenter.dto.UnifiedEventPayload;
import com.memoecho.eventcenter.model.AgentDispatchRetryJob;
import com.memoecho.eventcenter.model.StoredEvent;
import com.memoecho.eventcenter.repository.AgentDispatchRetryJobRepository;
import com.memoecho.eventcenter.repository.InMemoryEventRecordRepository;
import org.junit.jupiter.api.Test;

import java.time.Instant;
import java.util.List;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertFalse;
import static org.mockito.ArgumentMatchers.any;
import static org.mockito.ArgumentMatchers.anyInt;
import static org.mockito.BDDMockito.given;
import static org.mockito.Mockito.mock;
import static org.mockito.Mockito.verify;

class EventDispatchRetryTest {

    private final ObjectMapper objectMapper = new ObjectMapper();

    @Test
    void shouldPersistPendingStateAndRecoverAfterTransientRuntimeFailure() throws Exception {
        // 这个测试验证临时网络故障不会直接制造人工接管任务，并且后台重试成功后会恢复正常状态。
        InMemoryEventRecordRepository eventRepository = new InMemoryEventRecordRepository();
        AgentRuntimeDispatchClient dispatchClient = mock(AgentRuntimeDispatchClient.class);
        QqConnectorMessageClient qqClient = mock(QqConnectorMessageClient.class);
        AgentDispatchRetryJobRepository retryRepository = mock(AgentDispatchRetryJobRepository.class);
        AgentDispatchRetryProperties properties = retryProperties();
        EventCenterApplicationService service = new EventCenterApplicationService(
                eventRepository,
                dispatchClient,
                qqClient
        );
        service.setDispatchRetrySupport(retryRepository, properties);

        UnifiedEventPayload event = event("event-auto-retry");
        DispatchResult temporaryFailure = new DispatchResult(true, null, null, "Read timed out");
        DispatchResult recovered = new DispatchResult(
                true,
                200,
                objectMapper.readTree("""
                        {
                          "execution_id": "execution-retried",
                          "status": "completed",
                          "route": "social_reply",
                          "summary": "自动重试成功",
                          "final_reply": "收到",
                          "write_back_actions": ["qq_write_back_sent:ok"],
                          "results": []
                        }
                        """),
                null
        );
        given(dispatchClient.dispatch(any(UnifiedEventPayload.class)))
                .willReturn(temporaryFailure, recovered);

        service.ingest(event);

        StoredEvent pending = eventRepository.findByEventId(event.eventId()).orElseThrow();
        assertEquals("DISPATCH_RETRY_PENDING", pending.processingStatus());
        assertFalse(pending.needHumanConfirmation());
        verify(retryRepository).schedule(
                org.mockito.ArgumentMatchers.eq(event.eventId()),
                org.mockito.ArgumentMatchers.eq(1),
                any(Instant.class),
                org.mockito.ArgumentMatchers.eq("Read timed out"),
                any(Instant.class)
        );

        Instant dueAt = Instant.now().minusSeconds(1);
        AgentDispatchRetryJob dueJob = new AgentDispatchRetryJob(
                event.eventId(),
                "WAITING",
                1,
                dueAt,
                "Read timed out",
                dueAt,
                dueAt
        );
        given(retryRepository.findDue(any(Instant.class), anyInt())).willReturn(List.of(dueJob));
        given(retryRepository.claim(
                org.mockito.ArgumentMatchers.eq(event.eventId()),
                org.mockito.ArgumentMatchers.eq(1),
                any(Instant.class)
        )).willReturn(true);

        service.retryPendingRuntimeDispatches();

        StoredEvent completed = eventRepository.findByEventId(event.eventId()).orElseThrow();
        assertEquals("AUTO_RETRIED", completed.lastAction());
        assertFalse("DISPATCH_RETRY_PENDING".equals(completed.processingStatus()));
        verify(retryRepository).markSucceeded(
                org.mockito.ArgumentMatchers.eq(event.eventId()),
                any(Instant.class)
        );
    }

    /** 创建启用自动恢复且无需真实等待的测试配置。 */
    private AgentDispatchRetryProperties retryProperties() {
        AgentDispatchRetryProperties properties = new AgentDispatchRetryProperties();
        properties.setEnabled(true);
        properties.setMaxAttempts(3);
        properties.setInitialDelaySeconds(0);
        properties.setMaxDelaySeconds(0);
        properties.setBatchSize(10);
        return properties;
    }

    /** 创建一条可进入 Agent Runtime 的标准 QQ 私聊事件。 */
    private UnifiedEventPayload event(String eventId) {
        return new UnifiedEventPayload(
                eventId,
                "qq",
                "life",
                "message",
                "private",
                "3807050597",
                "3969785168",
                new SenderPayload("3807050597", "km", "member"),
                "下午好",
                List.of(),
                List.of(),
                "2026-07-16T08:00:00Z",
                objectMapper.createObjectNode()
        );
    }
}
