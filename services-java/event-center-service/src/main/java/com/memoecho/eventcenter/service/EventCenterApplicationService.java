package com.memoecho.eventcenter.service;

import com.fasterxml.jackson.databind.JsonNode;
import com.memoecho.eventcenter.dto.ConversationMessageResponse;
import com.memoecho.eventcenter.dto.ConversationOverviewResponse;
import com.memoecho.eventcenter.dto.ConversationSummaryResponse;
import com.memoecho.eventcenter.dto.DispatchResult;
import com.memoecho.eventcenter.dto.EventIngestResponse;
import com.memoecho.eventcenter.dto.StoredEventResponse;
import com.memoecho.eventcenter.dto.UnifiedEventPayload;
import com.memoecho.eventcenter.model.StoredEvent;
import com.memoecho.eventcenter.repository.EventRecordRepository;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.stereotype.Service;

import java.time.Duration;
import java.time.Instant;
import java.util.Comparator;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;
import java.util.Optional;

@Service
public class EventCenterApplicationService {

    private static final Logger log = LoggerFactory.getLogger(EventCenterApplicationService.class);

    private final EventRecordRepository repository;
    private final AgentRuntimeDispatchClient dispatchClient;

    public EventCenterApplicationService(EventRecordRepository repository, AgentRuntimeDispatchClient dispatchClient) {
        this.repository = repository;
        this.dispatchClient = dispatchClient;
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

        repository.save(new StoredEvent(event.eventId(), event, Instant.now()));
        DispatchResult dispatch = dispatchClient.dispatch(event);
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

    public Optional<StoredEventResponse> findByEventId(String eventId) {
        return repository.findByEventId(eventId).map(this::toStoredEventResponse);
    }

    public List<StoredEventResponse> findAll() {
        return repository.findAll().stream()
                .map(this::toStoredEventResponse)
                .toList();
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
                storedEvent.receivedAt().toString()
        );
    }

    private ConversationSummaryResponse toConversationSummary(StoredEvent storedEvent) {
        // 这里返回的是“列表摘要模型”，字段尽量贴近前端会话栏直接可用的形态。
        UnifiedEventPayload payload = storedEvent.payload();
        return new ConversationSummaryResponse(
                payload.platform(),
                payload.chatType(),
                payload.chatId(),
                deriveChatName(payload),
                payload.sender() != null ? payload.sender().name() : "",
                shorten(payload.text()),
                payload.timestamp(),
                deriveRoute(payload),
                deriveDispatchMode(payload),
                0,
                "urgent".equals(deriveDispatchMode(payload)) ? 1 : 0,
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
                false,
                false,
                deriveRoute(payload),
                deriveDispatchMode(payload)
        );
    }

    private Map<String, StoredEvent> latestConversationEvents() {
        Map<String, StoredEvent> latestByConversation = new LinkedHashMap<>();

        // repository.findAll() 已经按 receivedAt 倒序排好，
        // 所以每个会话第一次出现的事件就是最新那条。
        repository.findAll().forEach(event ->
                latestByConversation.putIfAbsent(conversationKey(event.payload()), event));

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
