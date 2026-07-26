package com.memoecho.eventcenter.service;

import com.fasterxml.jackson.databind.ObjectMapper;
import com.fasterxml.jackson.databind.node.JsonNodeFactory;
import com.memoecho.eventcenter.dto.MemoryCandidateResponse;
import com.memoecho.eventcenter.dto.MemoryCandidateUpsertRequest;
import com.memoecho.eventcenter.dto.MemoryConflictResolutionRequest;
import com.memoecho.eventcenter.dto.ConversationMessageResponse;
import com.memoecho.eventcenter.dto.UnifiedEventPayload;
import com.memoecho.eventcenter.model.MemoryCandidate;
import com.memoecho.eventcenter.model.StoredEvent;
import com.memoecho.eventcenter.repository.EventRecordRepository;
import com.memoecho.eventcenter.repository.MemoryCandidateRepository;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.extension.ExtendWith;
import org.mockito.Mock;
import org.mockito.junit.jupiter.MockitoExtension;
import org.springframework.web.server.ResponseStatusException;

import java.time.Instant;
import java.util.List;
import java.util.Optional;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertThrows;
import static org.mockito.ArgumentMatchers.any;
import static org.mockito.Mockito.when;
import static org.mockito.Mockito.verify;
import static org.mockito.Mockito.times;

/** 验证长期记忆状态流转、Runtime 来源约束和作用域匹配。 */
@ExtendWith(MockitoExtension.class)
class MemoryCandidateApplicationServiceTest {

    @Mock
    private MemoryCandidateRepository repository;

    @Mock
    private EventRecordRepository eventRecordRepository;

    @Mock
    private EventCenterApplicationService eventCenterApplicationService;

    private MemoryCandidateApplicationService applicationService;

    /** 每个测试使用真实 JSON 编解码器，确保证据事件格式可往返。 */
    @BeforeEach
    void setUp() {
        applicationService = new MemoryCandidateApplicationService(
                repository, eventRecordRepository, eventCenterApplicationService, new ObjectMapper());
    }

    /** Runtime 不能把 Agent 输出或联系人陈述登记为账号主人长期事实。 */
    @Test
    void shouldRejectUntrustedRuntimeCandidate() {
        MemoryCandidateUpsertRequest request = request("AGENT", "agent_output", "CONVERSATION");

        assertThrows(ResponseStatusException.class,
                () -> applicationService.createFromRuntime("freeze", request));
    }

    /** Runtime 候选必须保持 OWNER 与 human_self 来源，并以 CANDIDATE 状态落库。 */
    @Test
    void shouldCreateTrustedRuntimeCandidateWithoutAutoVerification() {
        trustSourceEvent("freeze", "OWNER", "USER_MANUAL");
        when(repository.save(any(MemoryCandidate.class))).thenAnswer(invocation -> invocation.getArgument(0));

        MemoryCandidateResponse response = applicationService.createFromRuntime(
                "freeze", request("OWNER", "human_self", "CONVERSATION"));

        assertEquals("CANDIDATE", response.status());
        assertEquals("OWNER", response.sourceActorType());
        assertEquals(List.of("event-1"), response.sourceEventIds());
    }

    /** 相同事实再次出现时应合并证据，而不是制造重复候选。 */
    @Test
    void shouldMergeDuplicateRuntimeCandidateEvidence() {
        trustSourceEvent("freeze", "OWNER", "USER_MANUAL");
        MemoryCandidate existing = candidate("candidate");
        when(repository.findActiveByFactKey(
                "freeze", "freeze", "常用称呼", "CONVERSATION", "qq", "life", "private", "friend-1"))
                .thenReturn(List.of(existing));
        when(repository.save(any(MemoryCandidate.class))).thenAnswer(invocation -> invocation.getArgument(0));

        MemoryCandidateResponse response = applicationService.createFromRuntime(
                "freeze", request("OWNER", "human_self", "CONVERSATION"));

        assertEquals("candidate", response.id());
        assertEquals(List.of("event-1"), response.sourceEventIds());
        assertEquals("CANDIDATE", response.status());
    }

    /** 请求体即使自报 OWNER，只要来源事件实际是 Agent 回显，也必须在服务端拒绝。 */
    @Test
    void shouldRejectRuntimeCandidateWhenStoredEvidenceIsNotManualOwnerMessage() {
        trustSourceEvent("freeze", "AGENT", "AGENT_AUTO");

        assertThrows(ResponseStatusException.class, () -> applicationService.createFromRuntime(
                "freeze", request("OWNER", "human_self", "CONVERSATION")));
    }

    /** 来源事件不能跨本地用户复用，避免一个账户的聊天事实污染另一个账户。 */
    @Test
    void shouldRejectRuntimeCandidateOwnedByAnotherUser() {
        trustSourceEvent("another-user", "OWNER", "USER_MANUAL");

        assertThrows(ResponseStatusException.class, () -> applicationService.createFromRuntime(
                "freeze", request("OWNER", "human_self", "CONVERSATION")));
    }

    /** 只有当前会话作用域匹配的记忆和全局记忆会提供给 Runtime。 */
    @Test
    void shouldFilterVerifiedMemoriesByScope() {
        when(repository.findVerifiedByUserId(any(), any())).thenReturn(List.of(
                memory("global", "GLOBAL", "", "", ""),
                memory("current", "CONVERSATION", "qq", "private", "friend-1"),
                memory("other", "CONVERSATION", "qq", "private", "friend-2")
        ));

        List<MemoryCandidateResponse> results = applicationService.listVerifiedForRuntime(
                "freeze", "qq", "life", "private", "friend-1");

        assertEquals(List.of("global", "current"), results.stream().map(MemoryCandidateResponse::id).toList());
    }

    /** 已确认记录不能再次编辑，避免覆盖掉用户曾确认过的事实。 */
    @Test
    void shouldRejectEditingVerifiedMemory() {
        when(repository.findByIdAndUserId("verified", "freeze"))
                .thenReturn(Optional.of(memory("verified", "GLOBAL", "", "", "")));

        assertThrows(ResponseStatusException.class,
                () -> applicationService.update("freeze", "verified", request("OWNER", "human_self", "GLOBAL")));
    }

    /** 编辑 Runtime 候选时必须保留原始事件证据，不能被桌面表单中的空来源覆盖。 */
    @Test
    void shouldPreserveSourceEvidenceWhenEditingCandidate() {
        MemoryCandidate candidate = candidate("candidate");
        when(repository.findByIdAndUserId("candidate", "freeze")).thenReturn(Optional.of(candidate));
        when(repository.save(any(MemoryCandidate.class))).thenAnswer(invocation -> invocation.getArgument(0));
        MemoryCandidateUpsertRequest edited = new MemoryCandidateUpsertRequest(
                "freeze", "常用称呼", "大橙子", "GLOBAL", "", "", "", "",
                List.of(), "OWNER", "human_self", 1.0d, null
        );

        MemoryCandidateResponse response = applicationService.update("freeze", "candidate", edited);

        assertEquals(List.of("event-1"), response.sourceEventIds());
        assertEquals(0.82d, response.confidence());
        assertEquals("OWNER", response.sourceActorType());
    }

    /** 有旧确认值时不能绕过冲突决策直接确认候选。 */
    @Test
    void shouldBlockDirectVerificationWhenVerifiedValueConflicts() {
        MemoryCandidate candidate = candidate("candidate");
        MemoryCandidate verified = memoryWithValue("verified", "旧称呼");
        when(repository.findByIdAndUserId("candidate", "freeze")).thenReturn(Optional.of(candidate));
        when(repository.findActiveByFactKey(
                "freeze", "freeze", "常用称呼", "CONVERSATION", "qq", "life", "private", "friend-1"))
                .thenReturn(List.of(candidate, verified));

        assertThrows(ResponseStatusException.class,
                () -> applicationService.verify("freeze", "candidate"));
    }

    /** 采用候选值时应在同一服务事务中替代旧值并确认新值。 */
    @Test
    void shouldSupersedeVerifiedValueWhenCandidateWinsConflict() {
        MemoryCandidate candidate = candidate("candidate");
        MemoryCandidate verified = memoryWithValue("verified", "旧称呼");
        when(repository.findByIdAndUserId("candidate", "freeze")).thenReturn(Optional.of(candidate));
        when(repository.findActiveByFactKey(
                "freeze", "freeze", "常用称呼", "CONVERSATION", "qq", "life", "private", "friend-1"))
                .thenReturn(List.of(candidate, verified));
        when(repository.save(any(MemoryCandidate.class))).thenAnswer(invocation -> invocation.getArgument(0));

        var response = applicationService.resolveConflict(
                "freeze", "candidate", new MemoryConflictResolutionRequest("USE_CANDIDATE"));

        assertEquals("VERIFIED", response.candidate().status());
        assertEquals(List.of("verified"), response.supersededMemoryIds());
        verify(repository, times(2)).save(any(MemoryCandidate.class));
    }

    /** 保留旧值时只拒绝新候选，不改变已确认事实。 */
    @Test
    void shouldRejectCandidateWhenVerifiedValueWinsConflict() {
        MemoryCandidate candidate = candidate("candidate");
        MemoryCandidate verified = memoryWithValue("verified", "旧称呼");
        when(repository.findByIdAndUserId("candidate", "freeze")).thenReturn(Optional.of(candidate));
        when(repository.findActiveByFactKey(
                "freeze", "freeze", "常用称呼", "CONVERSATION", "qq", "life", "private", "friend-1"))
                .thenReturn(List.of(candidate, verified));
        when(repository.save(any(MemoryCandidate.class))).thenAnswer(invocation -> invocation.getArgument(0));

        var response = applicationService.resolveConflict(
                "freeze", "candidate", new MemoryConflictResolutionRequest("KEEP_VERIFIED"));

        assertEquals("REJECTED", response.candidate().status());
        assertEquals(List.of(), response.supersededMemoryIds());
        verify(repository, times(1)).save(any(MemoryCandidate.class));
    }

    /** 来源证据视图会合并重叠上下文，并报告已经无法回查的来源事件。 */
    @Test
    void shouldMergeEvidenceContextsAndReportMissingSources() {
        MemoryCandidate candidate = new MemoryCandidate(
                "candidate", "freeze", "freeze", "常用称呼", "橙子", "CONVERSATION", "qq", "life",
                "private", "friend-1", "[\"event-1\",\"event-2\"]", "OWNER", "human_self", 0.82d,
                "CANDIDATE", "", Instant.parse("2026-07-17T00:00:00Z"), Instant.parse("2026-07-17T00:00:00Z"),
                null, Instant.parse("2026-07-17T00:00:00Z"), Instant.parse("2026-07-17T00:00:00Z")
        );
        ConversationMessageResponse message = conversationMessage("event-1", "2026-07-17T00:00:00Z");
        when(repository.findByIdAndUserId("candidate", "freeze")).thenReturn(Optional.of(candidate));
        when(eventCenterApplicationService.findConversationContextAroundEvent("freeze", "event-1", 2))
                .thenReturn(List.of(message));
        when(eventCenterApplicationService.findConversationContextAroundEvent("freeze", "event-2", 2))
                .thenReturn(List.of(message));

        var evidence = applicationService.evidence("freeze", "candidate", 2);

        assertEquals(List.of("event-1"), evidence.messages().stream().map(ConversationMessageResponse::eventId).toList());
        assertEquals(List.of("event-2"), evidence.missingEventIds());
    }

    /** 构造 Runtime 候选请求。 */
    private MemoryCandidateUpsertRequest request(String actorType, String authority, String scopeType) {
        return new MemoryCandidateUpsertRequest(
                "freeze", "常用称呼", "橙子", scopeType, "qq", "life", "private", "friend-1",
                List.of("event-1"), actorType, authority, 0.9d, null
        );
    }

    /** 构造一条未过期的已确认记忆。 */
    private MemoryCandidate memory(String id, String scopeType, String platform, String chatType, String chatId) {
        Instant now = Instant.parse("2026-07-17T00:00:00Z");
        return new MemoryCandidate(
                id, "freeze", "freeze", "常用称呼", "橙子", scopeType, platform, "", chatType, chatId,
                "[\"event-1\"]", "OWNER", "human_self", 0.95d, "VERIFIED", "",
                now, now, null, now, now
        );
    }

    /** 构造一条由 Runtime 抽取、仍等待用户确认的候选记忆。 */
    private MemoryCandidate candidate(String id) {
        Instant now = Instant.parse("2026-07-17T00:00:00Z");
        return new MemoryCandidate(
                id, "freeze", "freeze", "常用称呼", "橙子", "CONVERSATION", "qq", "life",
                "private", "friend-1", "[\"event-1\"]", "OWNER", "human_self", 0.82d,
                "CANDIDATE", "", now, now, null, now, now
        );
    }

    /** 构造与候选事实键相同、但内容不同的已确认记忆。 */
    private MemoryCandidate memoryWithValue(String id, String value) {
        Instant now = Instant.parse("2026-07-17T00:00:00Z");
        return new MemoryCandidate(
                id, "freeze", "freeze", "常用称呼", value, "CONVERSATION", "qq", "life",
                "private", "friend-1", "[\"event-old\"]", "OWNER", "human_self", 0.95d,
                "VERIFIED", "", now, now, null, now, now
        );
    }

    /** 构造证据预览所需的最小会话消息。 */
    private ConversationMessageResponse conversationMessage(String eventId, String timestamp) {
        return new ConversationMessageResponse(
                eventId, "qq", "private", "friend-1", "好友", "self-1", "freeze", "self", null,
                "大家叫我橙子", timestamp, List.of(), List.of(), true, false, "social_reply", "", "", "",
                "", false, "", "", null, "USER_MANUAL", List.of()
        );
    }

    /** 在事件仓储中准备一条可回查证据，模拟连接器与事件中心已经完成的来源标记。 */
    private void trustSourceEvent(String ownerUserId, String actorType, String messageOrigin) {
        UnifiedEventPayload payload = new UnifiedEventPayload(
                "event-1", "qq", "life", "message", "private", "friend-1", "self-1",
                null, "大家叫我橙子", List.of(), List.of(), "2026-07-17T00:00:00Z",
                JsonNodeFactory.instance.objectNode(), actorType, "message-1", "", "", 1L
        );
        StoredEvent event = StoredEvent.received(
                "event-1", ownerUserId, payload, Instant.parse("2026-07-17T00:00:00Z"))
                .withMessageOrigin(messageOrigin);
        when(eventRecordRepository.findByEventId("event-1")).thenReturn(Optional.of(event));
    }
}
