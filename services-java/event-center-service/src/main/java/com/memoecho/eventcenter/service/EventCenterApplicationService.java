package com.memoecho.eventcenter.service;

import com.fasterxml.jackson.databind.JsonNode;
import com.fasterxml.jackson.databind.node.JsonNodeFactory;
import com.fasterxml.jackson.databind.node.ObjectNode;
import com.memoecho.eventcenter.dto.ConversationMessageResponse;
import com.memoecho.eventcenter.dto.MediaAnalysisItem;
import com.memoecho.eventcenter.dto.MediaAnalysisResponse;
import com.memoecho.eventcenter.dto.MediaAnalysisUpdateRequest;
import com.memoecho.eventcenter.dto.ConversationDigestRequest;
import com.memoecho.eventcenter.dto.ConversationOverviewResponse;
import com.memoecho.eventcenter.dto.ConversationSummaryResponse;
import com.memoecho.eventcenter.dto.AgentExecutionStepResponse;
import com.memoecho.eventcenter.dto.DraftConfirmRequest;
import com.memoecho.eventcenter.dto.DraftRejectRequest;
import com.memoecho.eventcenter.dto.DispatchResult;
import com.memoecho.eventcenter.dto.EventIngestResponse;
import com.memoecho.eventcenter.dto.ExecutionTraceResponse;
import com.memoecho.eventcenter.dto.NotificationDecisionResponse;
import com.memoecho.eventcenter.dto.QqMessageSendResult;
import com.memoecho.eventcenter.dto.SnoozeEventRequest;
import com.memoecho.eventcenter.dto.StoredEventResponse;
import com.memoecho.eventcenter.dto.UnifiedEventPayload;
import com.memoecho.eventcenter.dto.SenderPayload;
import com.memoecho.eventcenter.dto.WorkspaceInboxItemResponse;
import com.memoecho.eventcenter.dto.WorkspaceStreamEventResponse;
import com.memoecho.eventcenter.config.AgentDispatchRetryProperties;
import com.memoecho.eventcenter.model.AgentDispatchRetryJob;
import com.memoecho.eventcenter.model.StoredEvent;
import com.memoecho.eventcenter.model.AgentExecutionStep;
import com.memoecho.eventcenter.model.ExecutionTrace;
import com.memoecho.eventcenter.model.NotificationDecision;
import com.memoecho.eventcenter.repository.EventRecordRepository;
import com.memoecho.eventcenter.repository.AgentDispatchRetryJobRepository;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.stereotype.Service;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.http.HttpStatus;
import org.springframework.scheduling.annotation.Scheduled;
import org.springframework.web.server.ResponseStatusException;

import java.time.Duration;
import java.time.Instant;
import java.util.ArrayList;
import java.util.Comparator;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;
import java.util.Optional;
import java.util.UUID;

@Service
public class EventCenterApplicationService {

    private static final Logger log = LoggerFactory.getLogger(EventCenterApplicationService.class);

    private final EventRecordRepository repository;
    private final AgentRuntimeDispatchClient dispatchClient;
    private final QqConnectorMessageClient qqConnectorMessageClient;
    private final WorkspaceEventStreamService workspaceEventStreamService;
    private final EventOwnershipResolver eventOwnershipResolver;
    private ConversationDigestBatchService digestBatchService;
    private AgentDispatchRetryJobRepository dispatchRetryRepository;
    private AgentDispatchRetryProperties dispatchRetryProperties;

    /** 可选注入摘要仓库；保留可选形式以兼容不启动 JDBC 上下文的轻量单元测试。 */
    @Autowired(required = false)
    public void setDigestBatchService(ConversationDigestBatchService digestBatchService) {
        this.digestBatchService = digestBatchService;
    }

    /**
     * 注入持久化重试组件。使用可选 Setter 是为了兼容现有纯单元测试，生产环境会由 Spring 正常注入。
     */
    @Autowired(required = false)
    public void setDispatchRetrySupport(
            AgentDispatchRetryJobRepository dispatchRetryRepository,
            AgentDispatchRetryProperties dispatchRetryProperties
    ) {
        this.dispatchRetryRepository = dispatchRetryRepository;
        this.dispatchRetryProperties = dispatchRetryProperties;
    }

    @Autowired
    public EventCenterApplicationService(
            EventRecordRepository repository,
            AgentRuntimeDispatchClient dispatchClient,
            QqConnectorMessageClient qqConnectorMessageClient,
            WorkspaceEventStreamService workspaceEventStreamService,
            EventOwnershipResolver eventOwnershipResolver
    ) {
        // 这个构造函数的作用是注入事件仓库、Runtime 派发器和 QQ Connector，使事件中心能够完成草稿确认闭环。
        this.repository = repository;
        this.dispatchClient = dispatchClient;
        this.qqConnectorMessageClient = qqConnectorMessageClient;
        this.workspaceEventStreamService = workspaceEventStreamService;
        this.eventOwnershipResolver = eventOwnershipResolver;
    }

    public EventCenterApplicationService(
            EventRecordRepository repository,
            AgentRuntimeDispatchClient dispatchClient,
            QqConnectorMessageClient qqConnectorMessageClient
    ) {
        // 这个构造函数的作用是兼容现有单元测试和独立使用场景，其中不需要创建真实 SSE 订阅。
        this(repository, dispatchClient, qqConnectorMessageClient, new WorkspaceEventStreamService(), null);
    }

    public EventIngestResponse ingest(UnifiedEventPayload event) {
        // 事件中心的入口职责很明确：接收标准事件、做幂等判断、再转发给 runtime。
        log.info("Event center received: eventId={}, platform={}, chatType={}, chatId={}, selfId={}",
                event.eventId(),
                event.platform(),
                event.chatType(),
                event.chatId(),
                event.selfId());
        if (repository.exists(event.eventId())) {
            log.info("Duplicate event ignored: eventId={}", event.eventId());
            return new EventIngestResponse(
                    event.eventId(),
                    true,
                    true,
                    new DispatchResult(false, null, null, null),
                    "Duplicate event ignored by event center."
            );
        }

        String ownerUserId = eventOwnershipResolver == null
                ? "local-user"
                : eventOwnershipResolver.resolveOwnerUserId(event);
        UnifiedEventPayload ownedEvent = withOwnerUserId(event, ownerUserId);
        StoredEvent receivedEvent = StoredEvent.received(event.eventId(), ownerUserId, ownedEvent, Instant.now())
                .withMessageOrigin(resolveIncomingMessageOrigin(ownedEvent, ownerUserId));
        repository.save(receivedEvent);

        // 开启 NapCat 的“上报自身消息”后，自己的消息要入库供上下文读取，但不能再次派给 Runtime。
        // 否则 Agent 发出的消息会被当作新入站消息，形成自我回复循环。
        if (isSelfReportedMessage(ownedEvent)) {
            StoredEvent archivedEvent = receivedEvent.markProcessed(
                    "SELF_MESSAGE_RECORDED",
                    "已保存当前账号发出的消息，仅用于后续上下文和授权的风格训练。",
                    "self_history",
                    "NOT_APPLICABLE",
                    false,
                    Instant.now(),
                    "",
                    null
            ).markInboxStatus("DONE", null, Instant.now());
            repository.save(archivedEvent);
            publishWorkspaceUpdate("conversation.self-message-recorded", archivedEvent);
            return new EventIngestResponse(
                    event.eventId(),
                    true,
                    false,
                    new DispatchResult(false, null, null, null),
                    "Self-reported message archived without runtime dispatch."
            );
        }

        DispatchResult dispatch = dispatchClient.dispatch(ownedEvent);
        StoredEvent processedEvent;
        if (shouldScheduleAutomaticRetry(dispatch, 1)) {
            Instant nextAttemptAt = nextRetryAt(1);
            dispatchRetryRepository.schedule(
                    receivedEvent.eventId(),
                    1,
                    nextAttemptAt,
                    dispatchError(dispatch),
                    Instant.now()
            );
            processedEvent = markDispatchRetryPending(receivedEvent, 1, nextAttemptAt, dispatch);
        } else {
            processedEvent = applyDispatchResult(receivedEvent, dispatch);
        }
        repository.save(processedEvent);
        publishWorkspaceUpdate("inbox.updated", processedEvent);
        log.info("Dispatched to agent runtime: attempted={}, httpStatus={}, error={}",
                dispatch.attempted(),
                dispatch.httpStatus(),
                dispatch.error());

        return new EventIngestResponse(
                event.eventId(),
                true,
                false,
                dispatch,
                "Event accepted by event center."
        );
    }

    /**
     * 把事件中心解析出的本地用户写入 Runtime 可见载荷，让设定集和模型配置按同一用户查询。
     */
    private UnifiedEventPayload withOwnerUserId(UnifiedEventPayload event, String ownerUserId) {
        if (ownerUserId == null || ownerUserId.isBlank() || "local-user".equals(ownerUserId)) {
            return event;
        }
        ObjectNode rawPayload = event.rawPayload() != null && event.rawPayload().isObject()
                ? ((ObjectNode) event.rawPayload()).deepCopy()
                : JsonNodeFactory.instance.objectNode();
        rawPayload.put("userId", ownerUserId);
        return new UnifiedEventPayload(
                event.eventId(), event.platform(), event.scene(), event.eventType(), event.chatType(),
                event.chatId(), event.selfId(), event.sender(), event.text(), event.attachments(),
                event.mentions(), event.timestamp(), rawPayload, event.actorType(), event.platformMessageId(),
                event.clientMessageId(), event.correlationId(), event.sequence(), event.sentAt(),
                event.receivedAt(), event.importedAt(), event.direction(), event.delegatedTaskId()
        );
    }

    /**
     * 区分手动消息与 Runtime/确认草稿产生的回显。只有 USER_MANUAL 会被个人风格提炼使用。
     * 对 selfId 回显，使用同会话、已发送草稿的文本匹配；确认发送与自动发送都会被排除。
     */
    private String resolveIncomingMessageOrigin(UnifiedEventPayload event, String ownerUserId) {
        if ("AGENT".equalsIgnoreCase(event.actorType())) {
            return "AGENT_AUTO";
        }
        if ("OWNER".equalsIgnoreCase(event.actorType())) {
            return "USER_MANUAL";
        }
        if ("CONTACT".equalsIgnoreCase(event.actorType()) || "SYSTEM".equalsIgnoreCase(event.actorType())) {
            return "EXTERNAL";
        }
        if (event.sender() == null || event.selfId() == null
                || !event.selfId().equals(event.sender().id())) {
            return "EXTERNAL";
        }
        return repository.findAll().stream()
                .filter(previous -> ownerUserId.equals(previous.ownerUserId()))
                .filter(previous -> event.platform().equals(previous.payload().platform()))
                .filter(previous -> event.chatType().equals(previous.payload().chatType()))
                .filter(previous -> event.chatId().equals(previous.payload().chatId()))
                .filter(previous -> "SENT".equals(previous.writeBackStatus())
                        || "DELAYED_SENT".equals(previous.writeBackStatus()))
                .filter(previous -> draftContainsMessage(previous.replyDraft(), event.text()))
                .findFirst()
                .map(previous -> "CONFIRMED".equals(previous.lastAction())
                        ? "AGENT_CONFIRMED" : "AGENT_AUTO")
                .orElse("USER_MANUAL");
    }

    /** 判断当前 Webhook 是否来自已登录账号；命中后只存档，不触发 Runtime。 */
    private boolean isSelfReportedMessage(UnifiedEventPayload event) {
        if (isWorkspaceCommand(event)) {
            return false;
        }
        if ("OWNER".equalsIgnoreCase(event.actorType()) || "AGENT".equalsIgnoreCase(event.actorType())) {
            return true;
        }
        if ("CONTACT".equalsIgnoreCase(event.actorType()) || "SYSTEM".equalsIgnoreCase(event.actorType())) {
            return false;
        }
        return event.sender() != null
                && event.selfId() != null
                && !event.selfId().isBlank()
                && event.selfId().equals(event.sender().id());
    }

    /**
     * 主控台命令虽然由当前用户发起，但它不是连接器回传的“自己发出的聊天消息”。
     * 这类事件必须继续派发到 Agent Runtime，否则客户端会误显示 Runtime 未启用。
     */
    private boolean isWorkspaceCommand(UnifiedEventPayload event) {
        return "desktop".equalsIgnoreCase(event.platform())
                && "desktop_command".equalsIgnoreCase(event.eventType());
    }

    private boolean draftContainsMessage(String draft, String text) {
        if (draft == null || text == null || text.isBlank()) {
            return false;
        }
        return java.util.Arrays.stream(draft.split("\\R"))
                .map(String::trim)
                .anyMatch(text.trim()::equals);
    }

    public StoredEventResponse recordConversationDigest(ConversationDigestRequest request) {
        // 这个函数的作用是将慢通道定时生成的摘要保存为合成事件，避免它再次进入 Runtime 形成循环处理。
        Instant now = Instant.now();
        if (digestBatchService != null) {
            String ownerUserId = request.ownerUserId() == null || request.ownerUserId().isBlank()
                    ? "default" : request.ownerUserId().trim();
            digestBatchService.save(ownerUserId, request);
        }
        String eventId = "digest:" + UUID.randomUUID();
        int messageCount = request.messageCount() == null ? 0 : Math.max(request.messageCount(), 0);
        NotificationDecision notification = new NotificationDecision(
                "normal",
                "NORMAL",
                "none",
                true,
                request.aggregationKey(),
                "SUMMARY_READY",
                messageCount,
                request.summary().trim()
        );
        ExecutionTrace trace = new ExecutionTrace(
                "digest:" + eventId,
                "chat_summary",
                "Slow channel digest generated.",
                List.of("workspace_digest_ready"),
                List.of(),
                notification
        );
        UnifiedEventPayload payload = new UnifiedEventPayload(
                eventId,
                request.platform(),
                request.scene(),
                "conversation_digest",
                request.chatType(),
                request.chatId(),
                request.selfId(),
                new SenderPayload("system", "Memo Echo", "system"),
                request.summary().trim(),
                List.of(),
                List.of(),
                now.toString(),
                null
        );
        StoredEvent digestEvent = StoredEvent.received(eventId, payload, now).markProcessed(
                "DIGEST_READY",
                "已生成 " + messageCount + " 条消息的会话摘要。",
                "chat_summary",
                "SILENT",
                false,
                now,
                "",
                trace
        );
        repository.save(digestEvent);
        publishWorkspaceUpdate("digest.ready", digestEvent);
        return toStoredEventResponse(digestEvent);
    }

    public Optional<StoredEventResponse> findByEventId(String eventId) {
        return repository.findByEventId(eventId).map(this::toStoredEventResponse);
    }

    /**
     * 校验事件是否属于当前登录用户，供高权限审批接口在代理 Runtime 前执行对象级鉴权。
     */
    public boolean isEventOwnedBy(String ownerUserId, String eventId) {
        return repository.findByEventId(eventId)
                .filter(event -> isOwnedConversationEvent(ownerUserId, event))
                .isPresent();
    }

    public List<StoredEventResponse> findAll() {
        // 这个函数的作用是兼容旧调用方，未传筛选条件时返回全部事件记录。
        return findAll(null);
    }

    public List<StoredEventResponse> findAll(String inboxStatus) {
        // 这个函数的作用是按当前有效收件箱状态筛选事件，支持工作台分别读取新消息、已读消息和稍后处理消息。
        return repository.findAll().stream()
                .filter(event -> matchesInboxStatus(event, inboxStatus))
                .map(this::toStoredEventResponse)
                .toList();
    }

    public List<WorkspaceInboxItemResponse> findWorkspaceInboxItems(String inboxStatus, Integer limit) {
        // 这个函数的作用是返回工作台收件箱所需的完整消息卡片，默认排除完成、忽略和未到期的稍后处理消息。
        int safeLimit = limit == null || limit <= 0 ? 50 : Math.min(limit, 200);
        return repository.findAll().stream()
                .filter(event -> inboxStatus == null || inboxStatus.isBlank()
                        ? isVisibleInInbox(event)
                        : matchesInboxStatus(event, inboxStatus))
                .limit(safeLimit)
                .map(this::toWorkspaceInboxItem)
                .toList();
    }

    /**
     * 只返回指定用户拥有的工作台消息，避免本地多账号之间共享收件箱。
     */
    public List<WorkspaceInboxItemResponse> findWorkspaceInboxItems(
            String ownerUserId,
            String inboxStatus,
            Integer limit
    ) {
        int safeLimit = limit == null || limit <= 0 ? 50 : Math.min(limit, 200);
        return repository.findAll().stream()
                .filter(event -> ownerUserId.equals(event.ownerUserId()))
                .filter(event -> inboxStatus == null || inboxStatus.isBlank()
                        ? isVisibleInInbox(event)
                        : matchesInboxStatus(event, inboxStatus))
                .limit(safeLimit)
                .map(this::toWorkspaceInboxItem)
                .toList();
    }

    public StoredEventResponse confirmDraft(String eventId, DraftConfirmRequest request) {
        // 这个函数的作用是确认指定事件的草稿，并把用户编辑后的文本安全发送回原始 QQ 会话。
        StoredEvent storedEvent = requireActionableDraft(eventId);
        String message = resolveDraftMessage(storedEvent, request != null ? request.message() : null);
        QqMessageSendResult sendResult = qqConnectorMessageClient.sendText(storedEvent.payload(), message);
        String note = request != null ? normalizeNote(request.note()) : "";

        StoredEvent updatedEvent;
        if (sendResult.successful()) {
            updatedEvent = storedEvent.markAction(
                    "MANUALLY_SENT",
                    "用户已确认发送草稿。",
                    "SENT",
                    false,
                    message,
                    "CONFIRMED",
                    appendOperationNote(note, sendResult.summary()),
                    Instant.now()
            ).markInboxStatus("DONE", null, Instant.now());
        } else {
            updatedEvent = storedEvent.markAction(
                    "SEND_FAILED",
                    "用户确认发送失败：" + sendResult.summary(),
                    "FAILED",
                    true,
                    message,
                    "CONFIRM_FAILED",
                    appendOperationNote(note, sendResult.summary()),
                    Instant.now()
            );
        }
        repository.save(updatedEvent);
        publishWorkspaceUpdate("inbox.updated", updatedEvent);
        return toStoredEventResponse(updatedEvent);
    }

    public StoredEventResponse rejectDraft(String eventId, DraftRejectRequest request) {
        // 这个函数的作用是拒绝指定草稿，不发送外部消息，但保留草稿和拒绝原因以便后续审计或重新编辑。
        StoredEvent storedEvent = requireActionableDraft(eventId);
        String reason = request != null ? normalizeNote(request.reason()) : "";
        StoredEvent updatedEvent = storedEvent.markAction(
                "DRAFT_REJECTED",
                "用户已拒绝发送草稿。",
                "REJECTED",
                false,
                storedEvent.replyDraft(),
                "REJECTED",
                reason,
                Instant.now()
        ).markInboxStatus("DONE", null, Instant.now());
        repository.save(updatedEvent);
        publishWorkspaceUpdate("inbox.updated", updatedEvent);
        return toStoredEventResponse(updatedEvent);
    }

    public StoredEventResponse markInboxRead(String eventId) {
        // 这个函数的作用是把消息标记为已读，但不把它从收件箱移除，适合用户已经浏览但还未决定如何处理的场景。
        return updateInboxStatus(eventId, "READ", null);
    }

    public StoredEventResponse markInboxDone(String eventId) {
        // 这个函数的作用是把消息标记为已处理，工作台重点摘要会自动排除这条消息。
        return updateInboxStatus(eventId, "DONE", null);
    }

    public StoredEventResponse ignoreInboxEvent(String eventId) {
        // 这个函数的作用是忽略不相关消息，避免它持续影响工作台的重点排序和建议动作。
        return updateInboxStatus(eventId, "IGNORED", null);
    }

    public StoredEventResponse snoozeInboxEvent(String eventId, SnoozeEventRequest request) {
        // 这个函数的作用是把消息延后处理；到期前它不会进入工作台重点消息列表。
        if (request == null || request.snoozedUntil() == null) {
            throw new ResponseStatusException(HttpStatus.BAD_REQUEST, "请提供稍后处理的截止时间。");
        }
        if (!request.snoozedUntil().isAfter(Instant.now())) {
            throw new ResponseStatusException(HttpStatus.BAD_REQUEST, "稍后处理时间必须晚于当前时间。");
        }
        return updateInboxStatus(eventId, "SNOOZED", request.snoozedUntil());
    }

    public StoredEventResponse retryEvent(String eventId) {
        // 这个函数的作用是对失败或待处理事件重新派发 Runtime，并将本次结果覆盖为最新可操作状态。
        StoredEvent storedEvent = requireEvent(eventId);
        if (!isRetryable(storedEvent)) {
            throw new ResponseStatusException(HttpStatus.CONFLICT, "当前事件不需要重试。");
        }
        DispatchResult dispatch = dispatchClient.dispatch(storedEvent.payload());
        int completedAttempts = dispatchRetryRepository == null
                ? 1
                : dispatchRetryRepository.findByEventId(eventId)
                .map(job -> job.attemptCount() + 1)
                .orElse(1);
        StoredEvent dispatchedEvent;
        if (shouldScheduleAutomaticRetry(dispatch, completedAttempts)) {
            Instant nextAttemptAt = nextRetryAt(completedAttempts);
            dispatchRetryRepository.schedule(
                    eventId,
                    completedAttempts,
                    nextAttemptAt,
                    dispatchError(dispatch),
                    Instant.now()
            );
            dispatchedEvent = markDispatchRetryPending(storedEvent, completedAttempts, nextAttemptAt, dispatch);
        } else {
            dispatchedEvent = applyDispatchResult(storedEvent, dispatch);
            finishRetryJob(eventId, dispatch, completedAttempts);
        }
        StoredEvent updatedEvent = dispatchedEvent.markAction(
                dispatchedEvent.processingStatus(),
                dispatchedEvent.processingSummary(),
                dispatchedEvent.writeBackStatus(),
                dispatchedEvent.needHumanConfirmation(),
                dispatchedEvent.replyDraft(),
                "RETRIED",
                "用户触发重新执行。",
                Instant.now()
        );
        repository.save(updatedEvent);
        publishWorkspaceUpdate("inbox.updated", updatedEvent);
        return toStoredEventResponse(updatedEvent);
    }

    /**
     * 定时领取到期的 Runtime 派发任务。任务状态存放在数据库中，Event Center 重启后仍可继续恢复。
     */
    @Scheduled(fixedDelayString = "${event-center.dispatch.retry.poll-interval-ms:1000}")
    public void retryPendingRuntimeDispatches() {
        if (!isAutomaticRetryEnabled()) {
            return;
        }
        Instant now = Instant.now();
        List<AgentDispatchRetryJob> dueJobs = dispatchRetryRepository.findDue(
                now,
                dispatchRetryProperties.getBatchSize()
        );
        for (AgentDispatchRetryJob job : dueJobs) {
            retryPendingRuntimeDispatch(job, now);
        }
    }

    /**
     * 执行单个已领取任务，并根据结果进入成功、继续等待或最终失败状态。
     */
    private void retryPendingRuntimeDispatch(AgentDispatchRetryJob job, Instant claimedAt) {
        if (!dispatchRetryRepository.claim(job.eventId(), job.attemptCount(), claimedAt)) {
            return;
        }
        Optional<StoredEvent> storedEvent = repository.findByEventId(job.eventId());
        if (storedEvent.isEmpty()) {
            dispatchRetryRepository.markDead(
                    job.eventId(),
                    job.attemptCount(),
                    "对应事件记录不存在，无法继续派发。",
                    Instant.now()
            );
            return;
        }

        int completedAttempts = job.attemptCount() + 1;
        DispatchResult dispatch = dispatchClient.dispatch(storedEvent.get().payload());
        if (isSuccessfulDispatch(dispatch)) {
            StoredEvent dispatched = applyDispatchResult(storedEvent.get(), dispatch);
            StoredEvent recovered = dispatched.markAction(
                    dispatched.processingStatus(),
                    dispatched.processingSummary(),
                    dispatched.writeBackStatus(),
                    dispatched.needHumanConfirmation(),
                    dispatched.replyDraft(),
                    "AUTO_RETRIED",
                    "Runtime 暂时不可用后已自动恢复。",
                    Instant.now()
            );
            repository.save(recovered);
            dispatchRetryRepository.markSucceeded(job.eventId(), Instant.now());
            publishWorkspaceUpdate("inbox.updated", recovered);
            log.info("Agent Runtime automatic retry succeeded: eventId={}, attempts={}",
                    job.eventId(), completedAttempts);
            return;
        }

        if (shouldScheduleAutomaticRetry(dispatch, completedAttempts)) {
            Instant nextAttemptAt = nextRetryAt(completedAttempts);
            dispatchRetryRepository.schedule(
                    job.eventId(),
                    completedAttempts,
                    nextAttemptAt,
                    dispatchError(dispatch),
                    Instant.now()
            );
            StoredEvent waiting = markDispatchRetryPending(
                    storedEvent.get(),
                    completedAttempts,
                    nextAttemptAt,
                    dispatch
            );
            repository.save(waiting);
            log.warn("Agent Runtime automatic retry postponed: eventId={}, attempts={}, nextAttemptAt={}, error={}",
                    job.eventId(), completedAttempts, nextAttemptAt, dispatchError(dispatch));
            return;
        }

        StoredEvent failed = applyDispatchResult(storedEvent.get(), dispatch).markAction(
                "DISPATCH_FAILED",
                deriveProcessingSummary(dispatch, "DISPATCH_FAILED", deriveWriteBackStatus(dispatch)),
                deriveWriteBackStatus(dispatch),
                false,
                storedEvent.get().replyDraft(),
                "AUTO_RETRY_EXHAUSTED",
                "Runtime 自动重试结束，需要用户检查服务状态后手动重试。",
                Instant.now()
        );
        repository.save(failed);
        dispatchRetryRepository.markDead(
                job.eventId(),
                completedAttempts,
                dispatchError(dispatch),
                Instant.now()
        );
        publishWorkspaceUpdate("inbox.updated", failed);
        log.error("Agent Runtime automatic retry exhausted: eventId={}, attempts={}, error={}",
                job.eventId(), completedAttempts, dispatchError(dispatch));
    }

    public ConversationOverviewResponse getConversationOverview() {
        // 概览接口直接复用会话列表结果，避免两套统计逻辑各自漂移。
        List<ConversationSummaryResponse> conversations = findConversationSummaries(null, null, null, null, null);

        int privateCount = (int) conversations.stream()
                .filter(conversation -> "private".equals(conversation.chatType()))
                .count();
        int groupCount = (int) conversations.stream()
                .filter(conversation -> "group".equals(conversation.chatType()))
                .count();
        int urgentCount = (int) conversations.stream()
                .filter(conversation -> "urgent".equals(conversation.lastDispatchMode()))
                .count();
        int summaryEnabledCount = (int) conversations.stream()
                .filter(ConversationSummaryResponse::summaryEnabled)
                .count();
        int activeLastHourCount = (int) repository.findAll().stream()
                .map(this::toConversationSummary)
                .filter(summary -> isActiveWithin(summary.lastMessageTime(), 60))
                .map(summary -> summary.platform() + ":" + summary.chatType() + ":" + summary.chatId())
                .distinct()
                .count();

        return new ConversationOverviewResponse(
                conversations.size(),
                privateCount,
                groupCount,
                urgentCount,
                summaryEnabledCount,
                activeLastHourCount
        );
    }

    public List<ConversationSummaryResponse> findConversationSummaries(
            String platform,
            String chatType,
            String keyword,
            String dispatchMode,
            Integer activeWithinMinutes
    ) {
        // 会话列表只需要每个会话最后一条事件，不需要完整事件时间线。
        Map<String, String> preferredNames = preferredConversationNames(null);
        return latestConversationEvents().values().stream()
                .map(event -> toConversationSummary(event, preferredNames.get(conversationKey(event.payload()))))
                .filter(summary -> matchesConversationSummary(summary, platform, chatType, keyword, dispatchMode, activeWithinMinutes))
                .sorted(Comparator.comparing(this::sortByLastActivity).reversed())
                .toList();
    }

    /**
     * 按本地用户读取会话摘要。用户过滤必须发生在“每个会话取最新事件”之前，
     * 否则不同用户具有相同平台会话 ID 时，较新的外部事件可能覆盖当前用户自己的会话。
     */
    public List<ConversationSummaryResponse> findConversationSummariesForUser(
            String ownerUserId,
            String platform,
            String chatType,
            String keyword,
            String dispatchMode,
            Integer activeWithinMinutes
    ) {
        Map<String, String> preferredNames = preferredConversationNames(ownerUserId);
        return latestConversationEvents(ownerUserId).values().stream()
                .map(event -> toConversationSummary(event, preferredNames.get(conversationKey(event.payload()))))
                .filter(summary -> matchesConversationSummary(
                        summary, platform, chatType, keyword, dispatchMode, activeWithinMinutes))
                .sorted(Comparator.comparing(this::sortByLastActivity).reversed())
                .toList();
    }

    public List<ConversationMessageResponse> findConversationMessages(
            String ownerUserId,
            String chatId,
            String platform,
            String chatType,
            Integer limit
    ) {
        return findConversationMessages(ownerUserId, chatId, platform, chatType, limit, null, null);
    }

    /**
     * 按会话时间窗读取消息。after 为包含边界，before 为排除边界，避免把任务创建前历史误作任务完成证据。
     * 历史读取失败时记录完整诊断（请求范围、HTTP 状态、响应正文、数据库命中数、过滤前后数量），
     * 并以 502 明确暴露给调用方，而不是静默返回空列表掩盖事实源问题。
     */
    public List<ConversationMessageResponse> findConversationMessages(
            String ownerUserId,
            String chatId,
            String platform,
            String chatType,
            Integer limit,
            String before,
            String after
    ) {
        // 这里做一个安全上限，避免前端或调试脚本一次拉太多消息。
        int safeLimit = limit == null || limit <= 0 ? 50 : Math.min(limit, 200);
        Instant beforeInstant = parseTimestamp(before);
        Instant afterInstant = parseTimestamp(after);

        List<StoredEvent> allEvents;
        try {
            allEvents = repository.findAll();
        } catch (Exception exception) {
            logHistoryFailure(ownerUserId, chatId, platform, chatType, safeLimit, before, after,
                    0, -1, -1, exception);
            throw historyQueryFailure("事件表查询失败。", chatId);
        }
        int dbHitCount = allEvents.size();

        List<StoredEvent> filtered;
        try {
            filtered = allEvents.stream()
                    .filter(event -> isOwnedConversationEvent(ownerUserId, event))
                    .filter(event -> matchesFilters(event.payload(), platform, chatType, chatId))
                    .filter(event -> isWithinConversationWindow(event, beforeInstant, afterInstant))
                    .toList();
        } catch (Exception exception) {
            logHistoryFailure(ownerUserId, chatId, platform, chatType, safeLimit, before, after,
                    dbHitCount, -1, -1, exception);
            throw historyQueryFailure("事件过滤失败。", chatId);
        }
        int filteredCount = filtered.size();

        try {
            return buildConversationTimeline(filtered, safeLimit, true);
        } catch (Exception exception) {
            logHistoryFailure(ownerUserId, chatId, platform, chatType, safeLimit, before, after,
                    dbHitCount, filteredCount, -1, exception);
            throw historyQueryFailure("事件时间线解析失败。", chatId);
        }
    }

    /**
     * 记录历史查询失败的完整诊断信息。
     * 覆盖请求范围、数据库命中数、过滤前后数量、拟返回的 HTTP 状态与响应正文。
     */
    private void logHistoryFailure(
            String ownerUserId,
            String chatId,
            String platform,
            String chatType,
            int limit,
            String before,
            String after,
            int dbHitCount,
            int filteredCount,
            int timelineCount,
            Exception exception
    ) {
        String requestScope = "userId=" + ownerUserId + ", chatId=" + chatId
                + ", platform=" + safeText(platform, "-") + ", chatType=" + safeText(chatType, "-")
                + ", limit=" + limit + ", before=" + safeText(before, "-")
                + ", after=" + safeText(after, "-");
        log.error(
                "历史查询失败 | 请求范围={} | dbHitCount={} | filteredBefore={} | filteredAfter={} | "
                        + "httpStatus=502 | responseBody={\"error\":\"history_query_failed\"} | errorType={} | message={}",
                requestScope,
                dbHitCount,
                filteredCount >= 0 ? filteredCount : -1,
                timelineCount >= 0 ? timelineCount : -1,
                exception.getClass().getSimpleName(),
                safeText(exception.getMessage(), exception.toString())
        );
    }

    /** 空值安全地回退到占位文本，避免诊断日志出现 null 字面量。 */
    private static String safeText(String value, String fallback) {
        return value == null || value.isBlank() ? fallback : value;
    }

    /** 构造历史查询失败的 502 响应，明确告知调用方不可静默使用空历史继续。 */
    private ResponseStatusException historyQueryFailure(String detail, String chatId) {
        return new ResponseStatusException(
                HttpStatus.BAD_GATEWAY, detail + " chatId=" + chatId);
    }

    /**
     * 兼容旧的工作台和测试调用。新 Runtime 调用必须使用带 ownerUserId 的重载方法，
     * 从而保证不同本地账户之间不会读取到彼此的私聊记录。
     */
    public List<ConversationMessageResponse> findConversationMessages(
            String chatId,
            String platform,
            String chatType,
            Integer limit
    ) {
        return findConversationMessages(chatId, platform, chatType, limit, null, null);
    }

    /** 兼容未加载本地用户上下文的测试环境，并支持与正式接口一致的时间窗口。 */
    public List<ConversationMessageResponse> findConversationMessages(
            String chatId,
            String platform,
            String chatType,
            Integer limit,
            String before,
            String after
    ) {
        int safeLimit = limit == null || limit <= 0 ? 50 : Math.min(limit, 200);
        Instant beforeInstant = parseTimestamp(before);
        Instant afterInstant = parseTimestamp(after);
        List<StoredEvent> conversationEvents = repository.findAll().stream()
                .filter(event -> matchesFilters(event.payload(), platform, chatType, chatId))
                .filter(event -> isWithinConversationWindow(event, beforeInstant, afterInstant))
                .toList();
        return buildConversationTimeline(conversationEvents, safeLimit, true);
    }

    /** 使用平台时间优先过滤，旧事件没有平台时间时退回接收时间。 */
    private boolean isWithinConversationWindow(StoredEvent event, Instant before, Instant after) {
        Instant occurredAt = resolveEventTimestamp(event);
        if (after != null && occurredAt.isBefore(after)) {
            return false;
        }
        return before == null || occurredAt.isBefore(before);
    }

    public Optional<ConversationMessageResponse> findOwnedSourceMessage(String ownerUserId, String eventId) {
        // 这个函数的作用是按用户归属读取一条原始事件，供日程来源校验和来源信息展示复用。
        if (eventId == null || eventId.isBlank()) {
            return Optional.empty();
        }
        return repository.findByEventId(eventId)
                .filter(event -> isOwnedConversationEvent(ownerUserId, event))
                .map(this::toConversationMessage);
    }

    public List<ConversationMessageResponse> findConversationContextAroundEvent(
            String ownerUserId,
            String eventId,
            Integer radius
    ) {
        // 这个函数的作用是围绕日程来源消息截取上下文，而不是把整段聊天历史暴露给客户端。
        StoredEvent sourceEvent = repository.findByEventId(eventId)
                .filter(event -> isOwnedConversationEvent(ownerUserId, event))
                .orElse(null);
        if (sourceEvent == null) {
            return List.of();
        }

        int safeRadius = radius == null ? 3 : Math.min(Math.max(radius, 0), 10);
        List<StoredEvent> conversationEvents = repository.findAll().stream()
                .filter(event -> isOwnedConversationEvent(ownerUserId, event))
                .filter(event -> conversationKey(event.payload()).equals(conversationKey(sourceEvent.payload())))
                .sorted(storedEventComparator())
                .toList();
        int sourceIndex = -1;
        for (int index = 0; index < conversationEvents.size(); index++) {
            if (conversationEvents.get(index).eventId().equals(eventId)) {
                sourceIndex = index;
                break;
            }
        }
        if (sourceIndex < 0) {
            return List.of(toConversationMessage(sourceEvent));
        }

        int fromIndex = Math.max(0, sourceIndex - safeRadius);
        int toIndex = Math.min(conversationEvents.size(), sourceIndex + safeRadius + 1);
        List<StoredEvent> contextEvents = conversationEvents.subList(fromIndex, toIndex);
        return buildConversationTimeline(contextEvents, Integer.MAX_VALUE, false);
    }

    /**
     * 将原始事件展开为稳定的会话时间线。平台时间决定主顺序，平台序号和事件 ID 负责消除同秒消息的不确定性。
     */
    private List<ConversationMessageResponse> buildConversationTimeline(
            List<StoredEvent> events,
            int limit,
            boolean newestFirst
    ) {
        Map<String, Integer> explicitAgentCorrelations = collectExplicitAgentCorrelationCounts(events);
        Map<String, Integer> legacySelfMessages = collectExplicitSelfMessageCounts(events);
        Map<String, Instant> arrivalTimes = new LinkedHashMap<>();
        for (StoredEvent event : events) {
            arrivalTimes.put(event.eventId(), event.receivedAt());
            List<String> sentParts = sentReplyParts(event);
            for (int index = 0; index < sentParts.size(); index++) {
                arrivalTimes.put(event.eventId() + ":reply:" + index, event.receivedAt());
            }
        }
        Comparator<ConversationMessageResponse> comparator = conversationMessageComparator(arrivalTimes);
        if (newestFirst) {
            comparator = comparator.reversed();
        }
        return events.stream()
                .flatMap(event -> toConversationTimeline(
                        event,
                        explicitAgentCorrelations,
                        legacySelfMessages
                ).stream())
                .sorted(comparator)
                .limit(limit)
                .toList();
    }

    /**
     * 构造事件稳定排序器。平台时间不可用时使用接收时间，最后用平台 sequence 与 eventId 打破平局。
     */
    private Comparator<StoredEvent> storedEventComparator() {
        return Comparator
                .comparing(this::resolveEventTimestamp)
                .thenComparing(event -> event.payload().sequence(), Comparator.nullsFirst(Long::compareTo))
                .thenComparing(StoredEvent::receivedAt)
                .thenComparing(StoredEvent::eventId);
    }

    /** 返回事件真实发生时间；旧事件缺少平台时间时才退回 Event Center 接收时间。 */
    private Instant resolveEventTimestamp(StoredEvent event) {
        Instant platformTimestamp = parseTimestamp(event.payload().timestamp());
        return platformTimestamp != null ? platformTimestamp : event.receivedAt();
    }

    /** 为展开后的消息构造确定性排序器。 */
    private Comparator<ConversationMessageResponse> conversationMessageComparator(Map<String, Instant> arrivalTimes) {
        return Comparator
                .comparing((ConversationMessageResponse message) -> {
                    Instant timestamp = parseTimestamp(message.timestamp());
                    return timestamp == null ? Instant.EPOCH : timestamp;
                })
                .thenComparing(ConversationMessageResponse::sequence, Comparator.nullsFirst(Long::compareTo))
                .thenComparing(message -> arrivalTimes.getOrDefault(message.eventId(), Instant.EPOCH))
                .thenComparing(ConversationMessageResponse::eventId);
    }

    /**
     * 校验事件归属。新事件直接按 ownerUserId 隔离；旧版 local-user 事件仅在平台账号仍属于当前用户时兼容读取。
     */
    private boolean isOwnedConversationEvent(String ownerUserId, StoredEvent event) {
        if (ownerUserId.equals(event.ownerUserId())) {
            return true;
        }
        return "local-user".equals(event.ownerUserId())
                && eventOwnershipResolver != null
                && eventOwnershipResolver.isConnectedAccountOwnedBy(ownerUserId, event.payload());
    }

    /**
     * 收集连接器已经明确上报的“我”方消息，避免同一条 Agent 回复既由回写草稿合成又由 Webhook 重复出现。
     */
    private Map<String, Integer> collectExplicitSelfMessageCounts(List<StoredEvent> events) {
        Map<String, Integer> counts = new LinkedHashMap<>();
        events.stream()
                .filter(event -> isSelfReportedMessage(event.payload()))
                // 新事件使用 correlationId 精确关联，不能再同时进入文本兜底计数。
                .filter(event -> event.payload().correlationId() == null
                        || event.payload().correlationId().isBlank())
                .map(event -> conversationMessageKey(event.payload(), event.payload().text()))
                .forEach(key -> counts.merge(key, 1, Integer::sum));
        return counts;
    }

    /**
     * 收集连接器明确标记的 Agent 回写关联。一个关联计数只抵消一个合成气泡，避免多段回复被整体吞掉。
     */
    private Map<String, Integer> collectExplicitAgentCorrelationCounts(List<StoredEvent> events) {
        Map<String, Integer> counts = new LinkedHashMap<>();
        events.stream()
                .map(StoredEvent::payload)
                .filter(payload -> "AGENT".equalsIgnoreCase(payload.actorType()))
                .map(UnifiedEventPayload::correlationId)
                .filter(correlationId -> correlationId != null && !correlationId.isBlank())
                .forEach(correlationId -> counts.merge(correlationId, 1, Integer::sum));
        return counts;
    }

    /**
     * 将一个事件展开成按时间倒序排列的会话片段：先返回较新的己方回复，再返回触发回复的对方消息。
     */
    private List<ConversationMessageResponse> toConversationTimeline(
            StoredEvent event,
            Map<String, Integer> explicitAgentCorrelations,
            Map<String, Integer> explicitSelfMessages
    ) {
        List<ConversationMessageResponse> timeline = new ArrayList<>();
        List<String> sentParts = sentReplyParts(event);
        for (int index = sentParts.size() - 1; index >= 0; index--) {
            String part = sentParts.get(index);
            int correlatedCount = explicitAgentCorrelations.getOrDefault(event.eventId(), 0);
            if (correlatedCount > 0) {
                // Runtime 发送消息时 correlationId 指向触发回复的入站 eventId，这是首选的精确去重路径。
                explicitAgentCorrelations.put(event.eventId(), correlatedCount - 1);
                continue;
            }
            String messageKey = conversationMessageKey(event.payload(), part);
            int explicitCount = explicitSelfMessages.getOrDefault(messageKey, 0);
            if (explicitCount > 0) {
                // 一个明确上报只能抵消一个合成回复；相同文本重复发送时不能把所有历史一起删掉。
                explicitSelfMessages.put(messageKey, explicitCount - 1);
            } else {
                timeline.add(toSentReplyMessage(event, part, index));
            }
        }
        timeline.add(toConversationMessage(event));
        return timeline;
    }

    /**
     * 只有平台确认发送成功的草稿才能进入历史，待审批、失败或仅生成草稿的内容绝不能伪装成真实消息。
     */
    private List<String> sentReplyParts(StoredEvent event) {
        if (!("SENT".equals(event.writeBackStatus()) || "DELAYED_SENT".equals(event.writeBackStatus()))
                || event.replyDraft() == null || event.replyDraft().isBlank()) {
            return List.of();
        }
        return java.util.Arrays.stream(event.replyDraft().split("\\R+"))
                .map(String::trim)
                .filter(part -> !part.isBlank())
                .toList();
    }

    /**
     * 构造一条只用于上下文读取的己方回复记录，不修改原事件，也不会再次触发 Runtime。
     */
    private ConversationMessageResponse toSentReplyMessage(StoredEvent event, String text, int partIndex) {
        UnifiedEventPayload payload = event.payload();
        Instant sentAt = event.lastActionAt() != null
                ? event.lastActionAt()
                : (event.processedAt() != null ? event.processedAt() : event.receivedAt());
        String messageOrigin = "CONFIRMED".equals(event.lastAction())
                ? "AGENT_CONFIRMED" : "AGENT_AUTO";
        return new ConversationMessageResponse(
                event.eventId() + ":reply:" + partIndex,
                payload.platform(),
                payload.chatType(),
                payload.chatId(),
                deriveChatName(payload),
                payload.selfId(),
                "我",
                "self",
                resolveSenderAvatar(payload.rawPayload(), payload.selfId()),
                text,
                sentAt.toString(),
                List.of(),
                List.of(),
                true,
                false,
                resolveStoredRoute(event),
                deriveDispatchMode(payload),
                event.processingStatus(),
                event.processingSummary(),
                event.writeBackStatus(),
                false,
                "",
                resolveInboxStatus(event),
                null,
                messageOrigin,
                List.of(),
                "AGENT",
                null,
                "synthetic:" + event.eventId() + ":" + partIndex,
                event.eventId(),
                null,
                sentAt.toString(),
                event.receivedAt().toString(),
                null,
                "OUTBOUND",
                payload.delegatedTaskId()
        );
    }

    /**
     * 为显式自身消息和合成回复生成稳定去重键，忽略仅由换行或空格造成的差异。
     */
    private String conversationMessageKey(UnifiedEventPayload payload, String text) {
        String normalizedText = text == null ? "" : text.replaceAll("\\s+", "").trim();
        return String.join("|", payload.platform(), payload.chatType(), payload.chatId(), normalizedText);
    }

    private StoredEventResponse toStoredEventResponse(StoredEvent storedEvent) {
        UnifiedEventPayload payload = storedEvent.payload();
        return new StoredEventResponse(
                storedEvent.eventId(),
                payload.platform(),
                payload.eventType(),
                payload.chatType(),
                payload.chatId(),
                payload.text(),
                payload.timestamp(),
                storedEvent.receivedAt().toString(),
                storedEvent.processingStatus(),
                storedEvent.processingSummary(),
                storedEvent.resolvedRoute(),
                storedEvent.writeBackStatus(),
                storedEvent.needHumanConfirmation(),
                storedEvent.processedAt() != null ? storedEvent.processedAt().toString() : null,
                storedEvent.replyDraft(),
                toExecutionTraceResponse(storedEvent.executionTrace()),
                storedEvent.lastAction(),
                storedEvent.lastActionNote(),
                storedEvent.lastActionAt() != null ? storedEvent.lastActionAt().toString() : null,
                resolveInboxStatus(storedEvent),
                storedEvent.inboxUpdatedAt() != null ? storedEvent.inboxUpdatedAt().toString() : null,
                storedEvent.snoozedUntil() != null ? storedEvent.snoozedUntil().toString() : null,
                storedEvent.messageOrigin()
        );
    }

    private WorkspaceInboxItemResponse toWorkspaceInboxItem(StoredEvent storedEvent) {
        // 这个函数的作用是将事件记录映射为工作台收件箱卡片，集中提供草稿、状态和操作入口需要的字段。
        UnifiedEventPayload payload = storedEvent.payload();
        return new WorkspaceInboxItemResponse(
                storedEvent.eventId(),
                payload.platform(),
                payload.chatType(),
                payload.chatId(),
                deriveChatName(payload),
                payload.sender() != null ? payload.sender().id() : "",
                payload.sender() != null ? payload.sender().name() : "",
                payload.text(),
                payload.timestamp(),
                resolveStoredRoute(storedEvent),
                storedEvent.processingStatus(),
                storedEvent.writeBackStatus(),
                storedEvent.replyDraft(),
                storedEvent.needHumanConfirmation(),
                isActionRequired(storedEvent),
                resolveInboxStatus(storedEvent),
                storedEvent.snoozedUntil() != null ? storedEvent.snoozedUntil().toString() : null,
                storedEvent.lastAction(),
                storedEvent.lastActionAt() != null ? storedEvent.lastActionAt().toString() : null,
                toNotificationDecisionResponse(storedEvent.executionTrace() != null
                        ? storedEvent.executionTrace().notification()
                        : null)
        );
    }

    private ConversationSummaryResponse toConversationSummary(StoredEvent storedEvent) {
        return toConversationSummary(storedEvent, deriveChatName(storedEvent.payload()));
    }

    /**
     * 这个函数的作用是使用会话历史中解析出的稳定名称生成列表摘要。
     * 最新事件可能是 Agent 代发消息，不能让它把联系人名称覆盖成本人昵称或纯 QQ 号。
     */
    private ConversationSummaryResponse toConversationSummary(StoredEvent storedEvent, String preferredChatName) {
        // 这里返回的是“列表摘要模型”，字段尽量贴近前端会话栏直接可用的形态。
        UnifiedEventPayload payload = storedEvent.payload();
        String dispatchMode = deriveDispatchMode(payload);
        return new ConversationSummaryResponse(
                payload.platform(),
                payload.chatType(),
                payload.chatId(),
                isUsableConversationName(preferredChatName, payload.chatId())
                        ? preferredChatName
                        : deriveChatName(payload),
                payload.sender() != null ? payload.sender().name() : "",
                shorten(payload.text()),
                payload.timestamp(),
                resolveStoredRoute(storedEvent),
                dispatchMode,
                storedEvent.processingStatus(),
                storedEvent.writeBackStatus(),
                isActionRequired(storedEvent),
                isUnread(storedEvent) ? 1 : 0,
                "urgent".equals(dispatchMode) ? 1 : 0,
                true,
                "group".equals(payload.chatType())
        );
    }

    private ConversationMessageResponse toConversationMessage(StoredEvent storedEvent) {
        // 这里返回的是“会话详情模型”，保留更完整的发送者、附件和路由信息。
        UnifiedEventPayload payload = storedEvent.payload();
        return new ConversationMessageResponse(
                storedEvent.eventId(),
                payload.platform(),
                payload.chatType(),
                payload.chatId(),
                deriveChatName(payload),
                payload.sender() != null ? payload.sender().id() : null,
                payload.sender() != null ? payload.sender().name() : null,
                payload.sender() != null ? payload.sender().role() : null,
                resolveSenderAvatar(
                        payload.rawPayload(),
                        payload.sender() != null ? payload.sender().id() : null
                ),
                payload.text(),
                payload.timestamp(),
                payload.mentions(),
                payload.attachments(),
                isProcessed(storedEvent),
                isReplied(storedEvent),
                resolveStoredRoute(storedEvent),
                deriveDispatchMode(payload),
                storedEvent.processingStatus(),
                storedEvent.processingSummary(),
                storedEvent.writeBackStatus(),
                storedEvent.needHumanConfirmation(),
                storedEvent.replyDraft(),
                resolveInboxStatus(storedEvent),
                storedEvent.snoozedUntil() != null ? storedEvent.snoozedUntil().toString() : null,
                storedEvent.messageOrigin(),
                extractMediaAnalysis(storedEvent.payload().rawPayload()),
                resolveActorType(payload),
                payload.platformMessageId(),
                payload.clientMessageId(),
                payload.correlationId(),
                payload.sequence(),
                firstNonBlank(payload.sentAt(), payload.timestamp()),
                firstNonBlank(payload.receivedAt(), storedEvent.receivedAt().toString()),
                payload.importedAt(),
                resolveDirection(payload),
                payload.delegatedTaskId()
        );
    }

    /**
     * 统一参与者身份。新连接器直接提供 actorType；旧事件才根据 selfId 与 senderId 推断。
     */
    private String resolveActorType(UnifiedEventPayload payload) {
        if (payload.actorType() != null && !payload.actorType().isBlank()) {
            return payload.actorType().trim().toUpperCase();
        }
        if (isSelfReportedMessage(payload)) {
            return payload.correlationId() != null && !payload.correlationId().isBlank()
                    ? "AGENT" : "OWNER";
        }
        return "CONTACT";
    }

    /**
     * 统一消息方向。连接器明确提供的方向优先，旧数据才根据参与者身份回退推断。
     */
    private String resolveDirection(UnifiedEventPayload payload) {
        if (payload.direction() != null && !payload.direction().isBlank()) {
            return payload.direction().trim().toUpperCase();
        }
        return switch (resolveActorType(payload)) {
            case "OWNER", "AGENT" -> "OUTBOUND";
            case "SYSTEM" -> "INTERNAL";
            default -> "INBOUND";
        };
    }

    /**
     * 优先读取 QCE 导出消息内嵌的头像；没有内嵌头像时再按 QQ 号生成公开头像地址。
     * 其他平台或无法确认数字账号时返回空值，前端会继续使用文字占位头像。
     */
    private String resolveSenderAvatar(JsonNode rawPayload, String senderId) {
        String embeddedAvatar = rawPayload == null
                ? ""
                : rawPayload.path("qceMessage").path("sender").path("avatarBase64").asText("").trim();
        if (!embeddedAvatar.isBlank()) {
            return embeddedAvatar.startsWith("data:")
                    ? embeddedAvatar
                    : "data:image/png;base64," + embeddedAvatar;
        }
        String safeSenderId = safeText(senderId);
        if (safeSenderId.matches("\\d{5,12}")) {
            return "https://q1.qlogo.cn/g?b=qq&nk=" + safeSenderId + "&s=100";
        }
        return null;
    }

    /**
     * 接收 Runtime 后台附件任务的结果，并追加到该事件原始载荷中。
     * 该操作不会重新派发 Agent，也不会覆盖原有草稿或人工审批状态。
     */
    public StoredEvent recordMediaAnalysis(String eventId, MediaAnalysisUpdateRequest request) {
        StoredEvent storedEvent = repository.findByEventId(eventId)
                .orElseThrow(() -> new ResponseStatusException(HttpStatus.NOT_FOUND, "事件不存在"));

        UnifiedEventPayload payload = storedEvent.payload();
        ObjectNode rawPayload = payload.rawPayload() != null && payload.rawPayload().isObject()
                ? ((ObjectNode) payload.rawPayload()).deepCopy()
                : JsonNodeFactory.instance.objectNode();
        var mediaAnalysis = rawPayload.putArray("mediaAnalysis");
        for (MediaAnalysisItem item : request.analyses()) {
            ObjectNode node = mediaAnalysis.addObject();
            node.put("attachmentId", safeText(item.attachmentId()));
            node.put("fileName", safeText(item.fileName()));
            node.put("fileType", safeText(item.fileType()));
            node.put("status", safeText(item.status()));
            node.put("summary", safeText(item.summary()));
            node.put("extractedText", safeText(item.extractedText()));
        }

        UnifiedEventPayload updatedPayload = new UnifiedEventPayload(
                payload.eventId(), payload.platform(), payload.scene(), payload.eventType(), payload.chatType(),
                payload.chatId(), payload.selfId(), payload.sender(), payload.text(), payload.attachments(),
                payload.mentions(), payload.timestamp(), rawPayload, payload.actorType(),
                payload.platformMessageId(), payload.clientMessageId(), payload.correlationId(), payload.sequence(),
                payload.sentAt(), payload.receivedAt(), payload.importedAt(), payload.direction(),
                payload.delegatedTaskId()
        );
        StoredEvent updatedEvent = storedEvent.withPayload(updatedPayload);
        repository.save(updatedEvent);
        publishWorkspaceUpdate("event.media_analyzed", updatedEvent);
        return updatedEvent;
    }

    /** 从 rawPayload 读取异步结果，兼容旧事件没有 mediaAnalysis 字段的情况。 */
    private List<MediaAnalysisResponse> extractMediaAnalysis(JsonNode rawPayload) {
        if (rawPayload == null || !rawPayload.path("mediaAnalysis").isArray()) {
            return List.of();
        }
        List<MediaAnalysisResponse> result = new ArrayList<>();
        for (JsonNode item : rawPayload.path("mediaAnalysis")) {
            result.add(new MediaAnalysisResponse(
                    text(item, "attachmentId"), text(item, "fileName"), text(item, "fileType"),
                    text(item, "status"), text(item, "summary"), text(item, "extractedText")
            ));
        }
        return result;
    }

    private String safeText(String value) {
        return value == null ? "" : value;
    }

    /** 返回第一个非空值，兼容尚未单独上报 sentAt/receivedAt 的旧事件。 */
    private String firstNonBlank(String preferred, String fallback) {
        return preferred == null || preferred.isBlank() ? fallback : preferred;
    }

    private StoredEvent applyDispatchResult(StoredEvent storedEvent, DispatchResult dispatch) {
        // 这个函数的作用是把 Agent Runtime 的返回结果折叠成事件中心自己的处理状态，供前端列表和工作台直接消费。
        String writeBackStatus = deriveWriteBackStatus(dispatch);
        boolean needHumanConfirmation = hasNeedHumanConfirmation(dispatch) || "CONFIRM_REQUIRED".equals(writeBackStatus);
        String processingStatus = deriveProcessingStatus(dispatch, writeBackStatus, needHumanConfirmation);
        String resolvedRoute = deriveRuntimeRoute(dispatch).orElseGet(() -> deriveRoute(storedEvent.payload()));
        String processingSummary = deriveProcessingSummary(dispatch, processingStatus, writeBackStatus);
        String replyDraft = deriveRuntimeReplyDraft(dispatch, storedEvent.replyDraft());
        ExecutionTrace executionTrace = deriveExecutionTrace(dispatch);

        return storedEvent.markProcessed(
                processingStatus,
                processingSummary,
                resolvedRoute,
                writeBackStatus,
                needHumanConfirmation,
                Instant.now(),
                replyDraft,
                executionTrace
        );
    }

    private ExecutionTrace deriveExecutionTrace(DispatchResult dispatch) {
        // 这个函数的作用是从 Runtime 返回中提取可展示的执行轨迹；刻意不保存工具参数、结构化结果、模型配置和系统提示词。
        if (dispatch.body() == null) {
            return null;
        }
        List<AgentExecutionStep> steps = new ArrayList<>();
        JsonNode results = dispatch.body().path("results");
        if (results.isArray()) {
            for (JsonNode result : results) {
                steps.add(new AgentExecutionStep(
                        result.path("agent").asText("unknown"),
                        result.path("status").asText("unknown"),
                        extractToolNames(result.path("tool_calls")),
                        extractSafeTextList(result.path("next_actions")),
                        result.path("need_confirmation").asBoolean(false)
                                || result.path("needConfirmation").asBoolean(false)
                ));
            }
        }
        return new ExecutionTrace(
                dispatch.body().path("execution_id").asText(""),
                dispatch.body().path("route").asText(""),
                dispatch.body().path("summary").asText(""),
                writeBackActions(dispatch).stream().map(this::sanitizeWriteBackAction).toList(),
                steps,
                deriveNotificationDecision(dispatch.body().path("notification")),
                extractSafeMemoryIds(dispatch.body().path("verified_memory_ids"))
        );
    }

    private List<String> extractSafeMemoryIds(JsonNode values) {
        // 这个函数的作用是只保留有限数量的记忆标识，不允许 Runtime 借审计字段写入正文或嵌套数据。
        if (!values.isArray()) {
            return List.of();
        }
        List<String> memoryIds = new ArrayList<>();
        for (JsonNode value : values) {
            if (!value.isTextual()) {
                continue;
            }
            String memoryId = value.asText("").trim();
            if (!memoryId.isBlank() && memoryId.length() <= 255 && !memoryIds.contains(memoryId)) {
                memoryIds.add(memoryId);
            }
            if (memoryIds.size() >= 100) {
                break;
            }
        }
        return memoryIds;
    }

    private NotificationDecision deriveNotificationDecision(JsonNode notification) {
        // 这个函数的作用是读取 Runtime 返回的通知决策；字段缺失时返回空值，兼容旧版 Runtime。
        if (!notification.isObject()) {
            return null;
        }
        return new NotificationDecision(
                notification.path("channel").asText("normal"),
                notification.path("priority").asText("NORMAL"),
                notification.path("trigger_reason").asText("none"),
                notification.path("notify_now").asBoolean(false),
                notification.path("aggregation_key").asText(""),
                notification.path("aggregation_status").asText("UNKNOWN"),
                notification.path("buffered_count").asInt(0),
                notification.path("summary_candidate").asText("")
        );
    }

    private List<String> extractToolNames(JsonNode toolCalls) {
        // 这个函数的作用是只读取工具名称，不保留工具参数，从源头避免文件内容或外部请求参数泄露到执行轨迹。
        if (!toolCalls.isArray()) {
            return List.of();
        }
        List<String> toolNames = new ArrayList<>();
        for (JsonNode toolCall : toolCalls) {
            String toolName = toolCall.path("tool").asText("").trim();
            if (!toolName.isBlank()) {
                toolNames.add(toolName);
            }
        }
        return toolNames;
    }

    private List<String> extractSafeTextList(JsonNode values) {
        // 这个函数的作用是提取 Runtime 的纯文本下一步动作；非文本节点会被忽略，避免把嵌套结果原样暴露。
        if (!values.isArray()) {
            return List.of();
        }
        List<String> textValues = new ArrayList<>();
        for (JsonNode value : values) {
            if (value.isTextual()) {
                textValues.add(value.asText());
            }
        }
        return textValues;
    }

    private String sanitizeWriteBackAction(String action) {
        // 这个函数的作用是保留回写动作类型而丢弃冒号后的运行时细节，避免异常文本意外带出敏感请求信息。
        if (action == null || action.isBlank()) {
            return "";
        }
        int separator = action.indexOf(':');
        return separator < 0 ? action : action.substring(0, separator);
    }

    private ExecutionTraceResponse toExecutionTraceResponse(ExecutionTrace executionTrace) {
        // 这个函数的作用是把内部执行轨迹映射为 API 响应模型，统一向前端暴露安全字段。
        if (executionTrace == null) {
            return null;
        }
        List<AgentExecutionStepResponse> steps = executionTrace.steps().stream()
                .map(step -> new AgentExecutionStepResponse(
                        step.agent(),
                        step.status(),
                        step.toolNames(),
                        step.nextActions(),
                        step.needHumanConfirmation()
                ))
                .toList();
        return new ExecutionTraceResponse(
                executionTrace.executionId(),
                executionTrace.route(),
                executionTrace.summary(),
                executionTrace.writeBackActions(),
                steps,
                toNotificationDecisionResponse(executionTrace.notification()),
                executionTrace.verifiedMemoryIds() == null ? List.of() : executionTrace.verifiedMemoryIds()
        );
    }

    private NotificationDecisionResponse toNotificationDecisionResponse(NotificationDecision notification) {
        // 这个函数的作用是把内部通知决策转为 API 模型，保证事件详情和收件箱使用同一份字段定义。
        if (notification == null) {
            return null;
        }
        return new NotificationDecisionResponse(
                notification.channel(),
                notification.priority(),
                notification.triggerReason(),
                notification.notifyNow(),
                notification.aggregationKey(),
                notification.aggregationStatus(),
                notification.bufferedCount(),
                notification.summaryCandidate()
        );
    }

    private StoredEvent requireEvent(String eventId) {
        // 这个函数的作用是统一处理事件不存在的情况，让三个工作台动作都返回明确的 404 响应。
        return repository.findByEventId(eventId)
                .orElseThrow(() -> new ResponseStatusException(HttpStatus.NOT_FOUND, "找不到指定事件。"));
    }

    private StoredEventResponse updateInboxStatus(String eventId, String inboxStatus, Instant snoozedUntil) {
        // 这个函数的作用是统一保存收件箱状态变更，确保所有操作都会更新同一套时间字段并返回最新事件响应。
        StoredEvent storedEvent = requireEvent(eventId);
        StoredEvent updatedEvent = storedEvent.markInboxStatus(inboxStatus, snoozedUntil, Instant.now());
        repository.save(updatedEvent);
        publishWorkspaceUpdate("inbox.updated", updatedEvent);
        return toStoredEventResponse(updatedEvent);
    }

    private void publishWorkspaceUpdate(String type, StoredEvent storedEvent) {
        // 这个函数的作用是将状态变化转为轻量 SSE 事件；完整卡片仍由收件箱接口按 eventId 查询。
        UnifiedEventPayload payload = storedEvent.payload();
        workspaceEventStreamService.publish(new WorkspaceStreamEventResponse(
                type,
                storedEvent.eventId(),
                payload.platform(),
                payload.selfId(),
                payload.chatType(),
                payload.chatId(),
                storedEvent.processingStatus(),
                resolveInboxStatus(storedEvent),
                isActionRequired(storedEvent),
                Instant.now().toString()
        ));
    }

    private StoredEvent requireActionableDraft(String eventId) {
        // 这个函数的作用是确保确认或拒绝只作用于尚未处理的草稿，避免重复发送已经确认过的消息。
        StoredEvent storedEvent = requireEvent(eventId);
        boolean actionable = storedEvent.needHumanConfirmation()
                || "DRAFT_READY".equals(storedEvent.processingStatus())
                || "CONFIRM_REQUIRED".equals(storedEvent.writeBackStatus())
                || "DRAFT_ONLY".equals(storedEvent.writeBackStatus());
        if (!actionable) {
            throw new ResponseStatusException(HttpStatus.CONFLICT, "当前事件没有可确认或拒绝的草稿。");
        }
        return storedEvent;
    }

    private String resolveDraftMessage(StoredEvent storedEvent, String requestedMessage) {
        // 这个函数的作用是优先使用用户编辑后的内容；未编辑时发送 Runtime 保存的原始草稿。
        String message = requestedMessage != null && !requestedMessage.isBlank()
                ? requestedMessage.trim()
                : storedEvent.replyDraft();
        if (message == null || message.isBlank() || "No reply was generated.".equals(message)) {
            throw new ResponseStatusException(HttpStatus.BAD_REQUEST, "当前事件没有可发送的草稿文本。");
        }
        return message;
    }

    private boolean isRetryable(StoredEvent storedEvent) {
        // 这个函数的作用是限制重试范围，只允许对派发、发送失败或尚未完成处理的事件再次执行。
        return "RECEIVED".equals(storedEvent.processingStatus())
                || "DISPATCH_RETRY_PENDING".equals(storedEvent.processingStatus())
                || "DISPATCH_FAILED".equals(storedEvent.processingStatus())
                || "SEND_FAILED".equals(storedEvent.processingStatus())
                || "FAILED".equals(storedEvent.writeBackStatus());
    }

    /** 判断自动重试组件是否完整启用。 */
    private boolean isAutomaticRetryEnabled() {
        return dispatchRetryRepository != null
                && dispatchRetryProperties != null
                && dispatchRetryProperties.isEnabled();
    }

    /**
     * 判断当前失败能否自动重试。4xx 参数或权限错误不会重试，避免无意义请求持续占用模型和平台资源。
     */
    private boolean isTransientDispatchFailure(DispatchResult dispatch) {
        if (!dispatch.attempted() || isSuccessfulDispatch(dispatch)) {
            return false;
        }
        Integer status = dispatch.httpStatus();
        if (status == null) {
            return true;
        }
        return status == 408 || status == 425 || status == 429 || status >= 500;
    }

    /** 判断 Runtime 是否返回了可落库的成功响应。 */
    private boolean isSuccessfulDispatch(DispatchResult dispatch) {
        return dispatch.attempted()
                && dispatch.error() == null
                && dispatch.httpStatus() != null
                && dispatch.httpStatus() >= 200
                && dispatch.httpStatus() < 400;
    }

    /** 根据已完成次数和最大次数判断是否继续安排下一次自动重试。 */
    private boolean shouldScheduleAutomaticRetry(DispatchResult dispatch, int completedAttempts) {
        return isAutomaticRetryEnabled()
                && isTransientDispatchFailure(dispatch)
                && completedAttempts < dispatchRetryProperties.getMaxAttempts();
    }

    /** 使用指数退避计算下一次执行时间，并受配置的最大延迟限制。 */
    private Instant nextRetryAt(int completedAttempts) {
        long multiplier = 1L << Math.min(Math.max(completedAttempts - 1, 0), 20);
        long delaySeconds = Math.min(
                dispatchRetryProperties.getInitialDelaySeconds() * multiplier,
                dispatchRetryProperties.getMaxDelaySeconds()
        );
        return Instant.now().plusSeconds(Math.max(delaySeconds, 0));
    }

    /** 把临时故障转换成不会触发人工接管的“等待自动恢复”事件状态。 */
    private StoredEvent markDispatchRetryPending(
            StoredEvent storedEvent,
            int completedAttempts,
            Instant nextAttemptAt,
            DispatchResult dispatch
    ) {
        return storedEvent.markProcessed(
                "DISPATCH_RETRY_PENDING",
                "Agent Runtime 暂时不可用，已完成第 " + completedAttempts
                        + " 次尝试，将在 " + nextAttemptAt + " 自动重试。",
                deriveRoute(storedEvent.payload()),
                "PENDING",
                false,
                null,
                storedEvent.replyDraft(),
                storedEvent.executionTrace()
        ).markAction(
                "DISPATCH_RETRY_PENDING",
                "Agent Runtime 暂时不可用，系统正在自动恢复。",
                "PENDING",
                false,
                storedEvent.replyDraft(),
                "AUTO_RETRY_SCHEDULED",
                dispatchError(dispatch),
                Instant.now()
        );
    }

    /** 返回适合日志和重试表保存的简短故障信息。 */
    private String dispatchError(DispatchResult dispatch) {
        if (dispatch.error() != null && !dispatch.error().isBlank()) {
            return dispatch.error();
        }
        return "HTTP " + dispatch.httpStatus();
    }

    /** 在手动重试产生最终结果后同步结束可能存在的后台任务。 */
    private void finishRetryJob(String eventId, DispatchResult dispatch, int completedAttempts) {
        if (dispatchRetryRepository == null) {
            return;
        }
        if (isSuccessfulDispatch(dispatch)) {
            dispatchRetryRepository.markSucceeded(eventId, Instant.now());
        } else {
            dispatchRetryRepository.markDead(
                    eventId,
                    completedAttempts,
                    dispatchError(dispatch),
                    Instant.now()
            );
        }
    }

    private String normalizeNote(String note) {
        // 这个函数的作用是清理用户输入的备注并限制长度，避免审计字段被异常超长文本撑大。
        if (note == null || note.isBlank()) {
            return "";
        }
        String normalized = note.trim();
        return normalized.length() <= 500 ? normalized : normalized.substring(0, 500);
    }

    private String appendOperationNote(String userNote, String platformSummary) {
        // 这个函数的作用是把用户备注与 Connector 的发送结果合并，便于前端在事件详情中展示本次操作结果。
        if (userNote == null || userNote.isBlank()) {
            return platformSummary == null ? "" : platformSummary;
        }
        if (platformSummary == null || platformSummary.isBlank()) {
            return userNote;
        }
        return userNote + "；Connector: " + platformSummary;
    }

    private String deriveProcessingStatus(
            DispatchResult dispatch,
            String writeBackStatus,
            boolean needHumanConfirmation
    ) {
        // 这个函数的作用是把底层 HTTP 派发结果转换成产品层能读懂的处理状态。
        if (!dispatch.attempted()) {
            return "RUNTIME_DISABLED";
        }
        if (dispatch.error() != null || dispatch.httpStatus() == null || dispatch.httpStatus() >= 400) {
            return "DISPATCH_FAILED";
        }
        if (needHumanConfirmation) {
            return "NEEDS_CONFIRMATION";
        }
        if ("DRAFT_ONLY".equals(writeBackStatus)) {
            return "DRAFT_READY";
        }
        if ("SENT".equals(writeBackStatus) || "DELAYED_SENT".equals(writeBackStatus)) {
            return "AUTO_REPLIED";
        }
        return "PROCESSED";
    }

    private String deriveWriteBackStatus(DispatchResult dispatch) {
        // 这个函数的作用是解析 runtime 的 write_back_actions，形成稳定的回写状态枚举字符串。
        if (!dispatch.attempted()) {
            return "DISABLED";
        }
        if (dispatch.error() != null || dispatch.httpStatus() == null || dispatch.httpStatus() >= 400) {
            return "UNKNOWN";
        }

        List<String> actions = writeBackActions(dispatch);
        if (actions.stream().anyMatch(action -> action.startsWith("qq_write_back_failed:"))) {
            return "FAILED";
        }
        boolean delayed = actions.stream().anyMatch(action -> action.startsWith("qq_write_back_delayed:"));
        boolean sent = actions.stream().anyMatch(action -> action.startsWith("qq_write_back_sent:"));
        if (delayed && sent) {
            return "DELAYED_SENT";
        }
        if (sent) {
            return "SENT";
        }
        if (actions.contains("qq_write_back_skipped:confirm_required")) {
            return "CONFIRM_REQUIRED";
        }
        if (actions.contains("qq_write_back_skipped:draft_only")) {
            return "DRAFT_ONLY";
        }
        if (actions.contains("qq_write_back_skipped:silent")) {
            return "SILENT";
        }
        if (actions.contains("qq_write_back_skipped:profile_inactive")) {
            return "PROFILE_INACTIVE";
        }
        if (actions.contains("qq_write_back_skipped:tool_not_registered")) {
            return "TOOL_NOT_REGISTERED";
        }
        return "NONE";
    }

    private String deriveProcessingSummary(
            DispatchResult dispatch,
            String processingStatus,
            String writeBackStatus
    ) {
        // 这个函数的作用是给状态补一段人能读懂的解释，后面前端可以直接展示在详情抽屉里。
        if (!dispatch.attempted()) {
            return "Agent Runtime 派发未开启，事件已留存在 event-center。";
        }
        if (dispatch.error() != null) {
            return "Agent Runtime 派发失败：" + dispatch.error();
        }
        if (dispatch.httpStatus() == null || dispatch.httpStatus() >= 400) {
            return "Agent Runtime 返回异常状态：" + dispatch.httpStatus();
        }
        String runtimeSummary = dispatch.body() != null ? dispatch.body().path("summary").asText("") : "";
        if (!runtimeSummary.isBlank()) {
            return runtimeSummary + "；processingStatus=" + processingStatus + "，writeBackStatus=" + writeBackStatus;
        }
        return "Agent Runtime 已处理完成；processingStatus=" + processingStatus + "，writeBackStatus=" + writeBackStatus;
    }

    private Optional<String> deriveRuntimeRoute(DispatchResult dispatch) {
        // 这个函数的作用是优先采用 Python runtime 的真实路由结果，而不是事件中心的启发式预判。
        if (dispatch.body() == null) {
            return Optional.empty();
        }
        String route = dispatch.body().path("route").asText("");
        return route.isBlank() ? Optional.empty() : Optional.of(route);
    }

    private String deriveRuntimeReplyDraft(DispatchResult dispatch, String existingDraft) {
        // 这个函数的作用是优先保存 Runtime 汇总出的 final_reply；兼容旧响应时再从单个 Agent 的 reply_draft 回退获取。
        if (dispatch.body() == null) {
            return existingDraft == null ? "" : existingDraft;
        }
        String finalReply = dispatch.body().path("final_reply").asText("").trim();
        if (!finalReply.isBlank() && !"No reply was generated.".equals(finalReply)) {
            return finalReply;
        }
        JsonNode results = dispatch.body().path("results");
        if (results.isArray()) {
            for (JsonNode result : results) {
                String replyDraft = result.path("reply_draft").asText("").trim();
                if (!replyDraft.isBlank()) {
                    return replyDraft;
                }
            }
        }
        return existingDraft == null ? "" : existingDraft;
    }

    private boolean hasNeedHumanConfirmation(DispatchResult dispatch) {
        // 这个函数的作用是兼容两类人工确认信号：整体回写策略和单个 Agent 结果。
        if (writeBackActions(dispatch).contains("qq_write_back_skipped:confirm_required")) {
            return true;
        }
        if (dispatch.body() == null || !dispatch.body().path("results").isArray()) {
            return false;
        }
        for (JsonNode result : dispatch.body().path("results")) {
            if (result.path("need_confirmation").asBoolean(false) || result.path("needConfirmation").asBoolean(false)) {
                return true;
            }
        }
        return false;
    }

    private List<String> writeBackActions(DispatchResult dispatch) {
        // 这个函数的作用是安全读取 runtime 返回的 write_back_actions，避免空 body 或字段类型错误影响主流程。
        if (dispatch.body() == null || !dispatch.body().path("write_back_actions").isArray()) {
            return List.of();
        }
        List<String> actions = new ArrayList<>();
        for (JsonNode action : dispatch.body().path("write_back_actions")) {
            if (action.isTextual()) {
                actions.add(action.asText());
            }
        }
        return actions;
    }

    private boolean isProcessed(StoredEvent storedEvent) {
        // 这个函数的作用是判断事件是否已经走过 runtime 派发阶段，包括成功、失败和功能关闭三种结果。
        return !"RECEIVED".equals(storedEvent.processingStatus());
    }

    private boolean isReplied(StoredEvent storedEvent) {
        // 这个函数的作用是判断消息是否已经真实回写到聊天平台，草稿和等待确认不算已回复。
        return "SENT".equals(storedEvent.writeBackStatus()) || "DELAYED_SENT".equals(storedEvent.writeBackStatus());
    }

    private boolean isActionRequired(StoredEvent storedEvent) {
        // 这个函数的作用是筛出需要用户进入工作台继续处理的事件，例如待确认草稿或派发失败。
        return isVisibleInInbox(storedEvent)
                && (storedEvent.needHumanConfirmation()
                || "DISPATCH_FAILED".equals(storedEvent.processingStatus())
                || "DRAFT_READY".equals(storedEvent.processingStatus())
                || "FAILED".equals(storedEvent.writeBackStatus())
                || "TOOL_NOT_REGISTERED".equals(storedEvent.writeBackStatus()));
    }

    private boolean matchesInboxStatus(StoredEvent storedEvent, String expectedInboxStatus) {
        // 这个函数的作用是让事件列表按“当前有效状态”筛选；已到期的 SNOOZED 会自动视作 NEW。
        return expectedInboxStatus == null || expectedInboxStatus.isBlank()
                || expectedInboxStatus.equalsIgnoreCase(resolveInboxStatus(storedEvent));
    }

    private boolean isUnread(StoredEvent storedEvent) {
        // 这个函数的作用是为会话摘要提供真实未读计数依据，只有 NEW 状态会计入未读。
        return "NEW".equals(resolveInboxStatus(storedEvent));
    }

    private boolean isVisibleInInbox(StoredEvent storedEvent) {
        // 这个函数的作用是决定事件是否继续参与工作台聚合；已完成、已忽略和未到期稍后处理都暂时隐藏。
        String inboxStatus = resolveInboxStatus(storedEvent);
        return !"DONE".equals(inboxStatus) && !"IGNORED".equals(inboxStatus) && !"SNOOZED".equals(inboxStatus);
    }

    private String resolveInboxStatus(StoredEvent storedEvent) {
        // 这个函数的作用是计算当前有效状态，稍后处理到期后无需后台任务即可自动恢复为 NEW。
        if ("SNOOZED".equals(storedEvent.inboxStatus())
                && storedEvent.snoozedUntil() != null
                && !storedEvent.snoozedUntil().isAfter(Instant.now())) {
            return "NEW";
        }
        return storedEvent.inboxStatus() == null || storedEvent.inboxStatus().isBlank()
                ? "NEW"
                : storedEvent.inboxStatus();
    }

    private String resolveStoredRoute(StoredEvent storedEvent) {
        // 这个函数的作用是优先返回 runtime 的真实路由；旧数据没有记录时再回退到本地启发式路由。
        if (storedEvent.resolvedRoute() != null && !storedEvent.resolvedRoute().isBlank()) {
            return storedEvent.resolvedRoute();
        }
        return deriveRoute(storedEvent.payload());
    }

    private Map<String, StoredEvent> latestConversationEvents() {
        // 旧版内部统计仍可读取全部用户的数据；用户侧功能必须调用带 ownerUserId 的重载。
        return latestConversationEvents(null);
    }

    /** 先执行用户隔离，再为每个会话保留最新事件。 */
    private Map<String, StoredEvent> latestConversationEvents(String ownerUserId) {
        Map<String, StoredEvent> latestByConversation = new LinkedHashMap<>();

        // repository.findAll() 已经按 receivedAt 倒序排好，
        // 所以每个会话第一次出现的事件就是最新那条。
        repository.findAll().stream()
                .filter(this::isVisibleInInbox)
                .filter(event -> ownerUserId == null || isOwnedConversationEvent(ownerUserId, event))
                .forEach(event -> latestByConversation.putIfAbsent(conversationKey(event.payload()), event));

        return latestByConversation;
    }

    /**
     * 这个函数的作用是从每个会话的历史入站事件中提取联系人或群名称。
     * 仓库按时间倒序返回事件，putIfAbsent 会优先保留最近一次真实、可读的名称。
     */
    private Map<String, String> preferredConversationNames(String ownerUserId) {
        Map<String, String> names = new LinkedHashMap<>();
        repository.findAll().stream()
                .filter(this::isVisibleInInbox)
                .filter(event -> ownerUserId == null || isOwnedConversationEvent(ownerUserId, event))
                .forEach(event -> {
                    UnifiedEventPayload payload = event.payload();
                    String candidate = deriveStableChatName(payload);
                    if (isUsableConversationName(candidate, payload.chatId())) {
                        names.putIfAbsent(conversationKey(payload), candidate.trim());
                    }
                });
        return names;
    }

    private boolean matchesConversationSummary(
            ConversationSummaryResponse summary,
            String platform,
            String chatType,
            String keyword,
            String dispatchMode,
            Integer activeWithinMinutes
    ) {
        // 把筛选逻辑集中在这里，controller 就能保持很薄，
        // 后面 UI 再加查询参数时也不用重复写规则。
        return matches(summary.platform(), platform)
                && matches(summary.chatType(), chatType)
                && matches(summary.lastDispatchMode(), dispatchMode)
                && matchesKeyword(summary, keyword)
                && matchesActivity(summary, activeWithinMinutes);
    }

    private boolean matchesFilters(UnifiedEventPayload payload, String platform, String chatType, String chatId) {
        return matches(payload.platform(), platform)
                && matches(payload.chatType(), chatType)
                && matches(payload.chatId(), chatId);
    }

    private boolean matches(String actual, String expected) {
        return expected == null || expected.isBlank() || expected.equalsIgnoreCase(actual);
    }

    private boolean matchesKeyword(ConversationSummaryResponse summary, String keyword) {
        if (keyword == null || keyword.isBlank()) {
            return true;
        }
        String normalizedKeyword = keyword.toLowerCase();
        return lower(summary.chatName()).contains(normalizedKeyword)
                || lower(summary.lastSenderName()).contains(normalizedKeyword)
                || lower(summary.lastMessage()).contains(normalizedKeyword);
    }

    private boolean matchesActivity(ConversationSummaryResponse summary, Integer activeWithinMinutes) {
        return activeWithinMinutes == null || activeWithinMinutes <= 0
                || isActiveWithin(summary.lastMessageTime(), activeWithinMinutes);
    }

    private boolean isActiveWithin(String timestamp, int minutes) {
        Instant messageInstant = parseTimestamp(timestamp);
        if (messageInstant == null) {
            return false;
        }
        return !messageInstant.isBefore(Instant.now().minus(Duration.ofMinutes(minutes)));
    }

    private Instant sortByLastActivity(ConversationSummaryResponse summary) {
        Instant messageInstant = parseTimestamp(summary.lastMessageTime());
        return messageInstant != null ? messageInstant : Instant.EPOCH;
    }

    private Instant parseTimestamp(String timestamp) {
        if (timestamp == null || timestamp.isBlank()) {
            return null;
        }
        try {
            return Instant.parse(timestamp);
        } catch (Exception ignored) {
            return null;
        }
    }

    private String conversationKey(UnifiedEventPayload payload) {
        return String.join(":", payload.platform(), payload.chatType(), payload.chatId());
    }

    private String deriveChatName(UnifiedEventPayload payload) {
        String stableName = deriveStableChatName(payload);
        return stableName.isBlank() ? payload.chatId() : stableName;
    }

    /**
     * 这个函数的作用是只从能够证明对方身份的字段中解析会话名称。
     * 私聊中 senderId 等于 chatId 才表示消息来自对方；Agent 或本人发出的消息不能用于命名联系人。
     */
    private String deriveStableChatName(UnifiedEventPayload payload) {
        JsonNode rawPayload = payload.rawPayload();
        if (rawPayload != null) {
            String groupName = text(rawPayload, "group_name");
            if (!groupName.isBlank()) {
                return groupName;
            }
        }
        if ("private".equals(payload.chatType())
                && payload.sender() != null
                && payload.chatId().equals(payload.sender().id())
                && payload.sender().name() != null) {
            return payload.sender().name();
        }
        return "";
    }

    /** 这个函数的作用是排除 QQ 号和旧版本占位符，只把可读文本作为会话名称。 */
    private boolean isUsableConversationName(String value, String chatId) {
        if (value == null || value.isBlank()) {
            return false;
        }
        String normalized = value.trim();
        return !normalized.equals(chatId)
                && !normalized.matches("\\d+")
                && !normalized.equalsIgnoreCase("unknown")
                && !normalized.equalsIgnoreCase("null")
                && !normalized.equalsIgnoreCase("undefined");
    }

    private String deriveRoute(UnifiedEventPayload payload) {
        String text = lower(payload.text());
        if (payload.attachments() != null && !payload.attachments().isEmpty()) {
            return "file_analysis";
        }
        // 这是给 UI 和联调用的临时启发式路由。
        // 真正路由结果仍然以 Python runtime 为准，后面可以替换掉。
        if (containsAny(text, List.of("today", "schedule", "meeting", "14:00", "deadline"))) {
            return "schedule_extract";
        }
        if (containsAny(text, List.of("plan", "todo", "task", "work"))) {
            return "task_plan";
        }
        if ("private".equals(payload.chatType())) {
            return "social_reply";
        }
        if (containsAny(text, List.of("notice", "welcome", "mute", "announce"))) {
            return "group_ops";
        }
        return "message_dispatch";
    }

    private String deriveDispatchMode(UnifiedEventPayload payload) {
        if ("private".equals(payload.chatType())) {
            return "urgent";
        }
        if (payload.selfId() != null && payload.mentions() != null && payload.mentions().contains(payload.selfId())) {
            return "urgent";
        }
        String text = lower(payload.text());
        if (containsAny(text, List.of(
                "\u901a\u77e5",
                "\u622a\u6b62",
                "\u62a5\u540d",
                "\u4f1a\u8bae",
                "\u5f00\u4f1a",
                "\u4eca\u5929",
                "\u660e\u5929",
                "notice",
                "deadline",
                "meeting"
        ))) {
            return "urgent";
        }
        return "normal";
    }

    private boolean containsAny(String text, List<String> keywords) {
        return keywords.stream().anyMatch(text::contains);
    }

    private String shorten(String text) {
        if (text == null || text.isBlank()) {
            return "";
        }
        return text.length() <= 80 ? text : text.substring(0, 80) + "...";
    }

    private String lower(String value) {
        return value == null ? "" : value.toLowerCase();
    }

    private String text(JsonNode node, String fieldName) {
        JsonNode value = node.path(fieldName);
        if (value.isMissingNode() || value.isNull()) {
            return "";
        }
        return value.asText("");
    }
}
