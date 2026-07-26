package com.memoecho.eventcenter.service;

import com.fasterxml.jackson.databind.JsonNode;
import com.memoecho.eventcenter.dto.ConversationCognitionCardResponse;
import com.memoecho.eventcenter.dto.ConversationCognitionCardUpsertRequest;
import com.memoecho.eventcenter.dto.ConversationMessageResponse;
import com.memoecho.eventcenter.model.ConversationCognitionCard;
import org.springframework.http.HttpStatus;
import org.springframework.stereotype.Service;
import org.springframework.web.server.ResponseStatusException;

import java.util.List;
import java.util.Objects;

/** 按用户动作刷新会话认知卡，并负责真实消息来源校验、增量去重和 Runtime 结果清洗。 */
@Service
public class ConversationCognitionRefreshService {

    private final EventCenterApplicationService eventCenterApplicationService;
    private final AgentRuntimeDispatchClient agentRuntimeDispatchClient;
    private final ConversationCognitionCardApplicationService cardApplicationService;

    /** 注入会话查询、Runtime 分析和认知卡合并服务，刷新链路不直接操作数据库。 */
    public ConversationCognitionRefreshService(
            EventCenterApplicationService eventCenterApplicationService,
            AgentRuntimeDispatchClient agentRuntimeDispatchClient,
            ConversationCognitionCardApplicationService cardApplicationService
    ) {
        this.eventCenterApplicationService = eventCenterApplicationService;
        this.agentRuntimeDispatchClient = agentRuntimeDispatchClient;
        this.cardApplicationService = cardApplicationService;
    }

    /**
     * 读取最新双向消息并按需分析。
     * 当事件 ID 列表与上次完全一致时直接返回旧卡，避免用户重复打开页面造成模型重复计费。
     */
    public ConversationCognitionCardResponse refresh(
            String userId,
            String platform,
            String chatType,
            String chatId,
            Integer limit
    ) {
        int safeLimit = limit == null || limit <= 0 ? 80 : Math.min(limit, 120);
        List<ConversationMessageResponse> messages = eventCenterApplicationService.findConversationMessages(
                userId, chatId, platform, chatType, safeLimit);
        List<String> sourceEventIds = messages.stream()
                .map(ConversationMessageResponse::eventId)
                .filter(Objects::nonNull)
                .map(String::trim)
                .filter(value -> !value.isBlank())
                .distinct()
                .toList();
        if (messages.isEmpty()) {
            return cardApplicationService.find(userId, platform, chatType, chatId)
                    .orElseThrow(() -> new ResponseStatusException(
                            HttpStatus.UNPROCESSABLE_ENTITY, "当前会话没有可用于分析的消息"));
        }

        ConversationCognitionCardResponse existing = cardApplicationService
                .find(userId, platform, chatType, chatId)
                .orElse(null);
        if (existing != null && existing.sourceEventIds().equals(sourceEventIds)) {
            return existing;
        }

        JsonNode runtimeResult = agentRuntimeDispatchClient.analyzeConversationCognition(
                userId, platform, chatType, chatId, messages);
        if (runtimeResult == null || runtimeResult.isMissingNode() || runtimeResult.isNull()) {
            throw new ResponseStatusException(HttpStatus.BAD_GATEWAY, "会话认知分析服务暂时不可用，旧认知卡已保留");
        }

        boolean generatedByModel = runtimeResult.path("generatedByModel").asBoolean(false);
        ConversationCognitionCardUpsertRequest request = new ConversationCognitionCardUpsertRequest(
                platform,
                chatType,
                chatId,
                generatedByModel ? readField(runtimeResult, "relationship") : null,
                generatedByModel ? readField(runtimeResult, "preferredAddress") : null,
                generatedByModel ? readField(runtimeResult, "counterpartyTraits") : null,
                generatedByModel ? readField(runtimeResult, "ownerExpressionHabits") : null,
                generatedByModel ? readField(runtimeResult, "counterpartyExpressionHabits") : null,
                generatedByModel ? readField(runtimeResult, "backgroundSummary") : null,
                readField(runtimeResult, "currentProgress"),
                generatedByModel ? readTextList(runtimeResult, "knownFacts") : null,
                generatedByModel ? readTextList(runtimeResult, "recentTopics") : null,
                generatedByModel ? readTextList(runtimeResult, "openQuestions") : null,
                sourceEventIds,
                messages.size()
        );
        return cardApplicationService.upsertInference(userId, request);
    }

    /** 从 Runtime JSON 中读取一个认知字段；字段来源和锁定状态仍由 Java 应用层强制确定。 */
    private ConversationCognitionCard.CognitionField readField(JsonNode root, String fieldName) {
        JsonNode node = root.path(fieldName);
        if (!node.isObject()) {
            return null;
        }
        String value = node.path("value").asText("").trim();
        double confidence = node.path("confidence").asDouble(0.0d);
        return new ConversationCognitionCard.CognitionField(value, "AI_INFERRED", confidence, false);
    }

    /** 只接收 Runtime 返回的字符串数组，非文本元素不会进入认知卡。 */
    private List<String> readTextList(JsonNode root, String fieldName) {
        JsonNode node = root.path(fieldName);
        if (!node.isArray()) {
            return List.of();
        }
        java.util.ArrayList<String> result = new java.util.ArrayList<>();
        node.forEach(item -> {
            if (item.isTextual() && !item.asText().isBlank()) {
                result.add(item.asText().trim());
            }
        });
        return List.copyOf(result);
    }
}
