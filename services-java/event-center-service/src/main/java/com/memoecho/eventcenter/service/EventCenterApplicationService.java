package com.memoecho.eventcenter.service;

import com.fasterxml.jackson.databind.JsonNode;
import com.memoecho.eventcenter.dto.ConversationMessageResponse;
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
import com.memoecho.eventcenter.model.StoredEvent;
import com.memoecho.eventcenter.model.AgentExecutionStep;
import com.memoecho.eventcenter.model.ExecutionTrace;
import com.memoecho.eventcenter.model.NotificationDecision;
import com.memoecho.eventcenter.repository.EventRecordRepository;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.stereotype.Service;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.http.HttpStatus;
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

    @Autowired
    public EventCenterApplicationService(
            EventRecordRepository repository,
            AgentRuntimeDispatchClient dispatchClient,
            QqConnectorMessageClient qqConnectorMessageClient,
            WorkspaceEventStreamService workspaceEventStreamService
    ) {
        // 这个构造函数的作用是注入事件仓库、Runtime 派发器和 QQ Connector，使事件中心能够完成草稿确认闭环。
        this.repository = repository;
        this.dispatchClient = dispatchClient;
        this.qqConnectorMessageClient = qqConnectorMessageClient;
        this.workspaceEventStreamService = workspaceEventStreamService;
    }

    public EventCenterApplicationService(
            EventRecordRepository repository,
            AgentRuntimeDispatchClient dispatchClient,
            QqConnectorMessageClient qqConnectorMessageClient
    ) {
        // 这个构造函数的作用是兼容现有单元测试和独立使用场景，其中不需要创建真实 SSE 订阅。
        this(repository, dispatchClient, qqConnectorMessageClient, new WorkspaceEventStreamService());
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

        StoredEvent receivedEvent = StoredEvent.received(event.eventId(), event, Instant.now());
        repository.save(receivedEvent);
        DispatchResult dispatch = dispatchClient.dispatch(event);
        StoredEvent processedEvent = applyDispatchResult(receivedEvent, dispatch);
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

    public StoredEventResponse recordConversationDigest(ConversationDigestRequest request) {
        // 这个函数的作用是将慢通道定时生成的摘要保存为合成事件，避免它再次进入 Runtime 形成循环处理。
        Instant now = Instant.now();
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
        StoredEvent dispatchedEvent = applyDispatchResult(storedEvent, dispatch);
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
        return latestConversationEvents().values().stream()
                .map(this::toConversationSummary)
                .filter(summary -> matchesConversationSummary(summary, platform, chatType, keyword, dispatchMode, activeWithinMinutes))
                .sorted(Comparator.comparing(this::sortByLastActivity).reversed())
                .toList();
    }

    public List<ConversationMessageResponse> findConversationMessages(
            String chatId,
            String platform,
            String chatType,
            Integer limit
    ) {
        // 这里做一个安全上限，避免前端或调试脚本一次拉太多消息。
        int safeLimit = limit == null || limit <= 0 ? 50 : Math.min(limit, 200);

        return repository.findAll().stream()
                .filter(event -> matchesFilters(event.payload(), platform, chatType, chatId))
                .limit(safeLimit)
                .map(this::toConversationMessage)
                .toList();
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
                storedEvent.snoozedUntil() != null ? storedEvent.snoozedUntil().toString() : null
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
        // 这里返回的是“列表摘要模型”，字段尽量贴近前端会话栏直接可用的形态。
        UnifiedEventPayload payload = storedEvent.payload();
        String dispatchMode = deriveDispatchMode(payload);
        return new ConversationSummaryResponse(
                payload.platform(),
                payload.chatType(),
                payload.chatId(),
                deriveChatName(payload),
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
                storedEvent.snoozedUntil() != null ? storedEvent.snoozedUntil().toString() : null
        );
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
                deriveNotificationDecision(dispatch.body().path("notification"))
        );
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
                toNotificationDecisionResponse(executionTrace.notification())
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
                || "DISPATCH_FAILED".equals(storedEvent.processingStatus())
                || "SEND_FAILED".equals(storedEvent.processingStatus())
                || "FAILED".equals(storedEvent.writeBackStatus());
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
        Map<String, StoredEvent> latestByConversation = new LinkedHashMap<>();

        // repository.findAll() 已经按 receivedAt 倒序排好，
        // 所以每个会话第一次出现的事件就是最新那条。
        repository.findAll().stream()
                .filter(this::isVisibleInInbox)
                .forEach(event -> latestByConversation.putIfAbsent(conversationKey(event.payload()), event));

        return latestByConversation;
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
        JsonNode rawPayload = payload.rawPayload();
        if (rawPayload != null) {
            String groupName = text(rawPayload, "group_name");
            if (!groupName.isBlank()) {
                return groupName;
            }
        }
        if ("private".equals(payload.chatType()) && payload.sender() != null && payload.sender().name() != null) {
            return payload.sender().name();
        }
        return payload.chatId();
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
