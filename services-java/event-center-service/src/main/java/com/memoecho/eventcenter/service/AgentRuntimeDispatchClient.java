package com.memoecho.eventcenter.service;

import com.fasterxml.jackson.databind.JsonNode;
import com.memoecho.eventcenter.config.AgentRuntimeDispatchProperties;
import com.memoecho.eventcenter.dto.DispatchResult;
import com.memoecho.eventcenter.dto.ConversationMessageResponse;
import com.memoecho.eventcenter.dto.ConversationSummaryResponse;
import com.memoecho.eventcenter.dto.DelegatedTaskCompilationResponse;
import com.memoecho.eventcenter.dto.DelegatedWorkflowStepExecutionRequest;
import com.memoecho.eventcenter.dto.UnifiedEventPayload;
import org.springframework.http.ResponseEntity;
import org.springframework.stereotype.Component;
import org.springframework.web.client.RestClient;
import org.springframework.web.client.RestClientException;
import org.springframework.web.client.RestClientResponseException;
import org.springframework.web.util.UriUtils;

import java.nio.charset.StandardCharsets;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;

@Component
public class AgentRuntimeDispatchClient {

    private final RestClient restClient;
    private final AgentRuntimeDispatchProperties properties;

    public AgentRuntimeDispatchClient(RestClient restClient, AgentRuntimeDispatchProperties properties) {
        this.restClient = restClient;
        this.properties = properties;
    }

    public DispatchResult dispatch(UnifiedEventPayload payload) {
        if (!properties.isEnabled()) {
            return new DispatchResult(false, null, null, null);
        }

        try {
            ResponseEntity<JsonNode> response = restClient.post()
                    .uri(properties.getBaseUrl() + properties.getHandlePath())
                    .body(payload)
                    .retrieve()
                    .toEntity(JsonNode.class);

            return new DispatchResult(
                    true,
                    response.getStatusCode().value(),
                    response.getBody(),
                    null
            );
        } catch (RestClientResponseException ex) {
            // 保留 HTTP 状态码后，调度层才能区分可重试的 5xx/429 与不应重试的请求错误。
            return new DispatchResult(true, ex.getStatusCode().value(), null, ex.getMessage());
        } catch (RestClientException ex) {
            return new DispatchResult(true, null, null, ex.getMessage());
        }
    }

    /**
     * 显式执行一个工作流步骤，并把 HTTP 结果完整交给 outbox 调度器判断是否重试。
     */
    public DispatchResult executeDelegatedWorkflowStep(DelegatedWorkflowStepExecutionRequest request) {
        if (!properties.isEnabled()) {
            return new DispatchResult(false, null, null, "Agent Runtime 当前未启用");
        }
        try {
            ResponseEntity<JsonNode> response = restClient.post()
                    .uri(properties.getBaseUrl() + properties.getDelegatedWorkflowStepExecutePath())
                    .body(request)
                    .retrieve()
                    .toEntity(JsonNode.class);
            return new DispatchResult(true, response.getStatusCode().value(), response.getBody(), null);
        } catch (RestClientResponseException exception) {
            return new DispatchResult(
                    true, exception.getStatusCode().value(), null, exception.getResponseBodyAsString());
        } catch (RestClientException exception) {
            return new DispatchResult(true, null, null, exception.getMessage());
        }
    }

    /**
     * 让 Python LangGraph 把自然语言命令编译成结构化任务契约。
     * Java 只提供当前用户有权访问的会话候选，因此模型不能绑定任意账号或群聊。
     */
    public DelegatedTaskCompilationResponse compileDelegatedTask(
            String userId, String command, List<ConversationSummaryResponse> conversations
    ) {
        if (!properties.isEnabled()) {
            return null;
        }
        Map<String, Object> payload = new LinkedHashMap<>();
        payload.put("userId", userId);
        payload.put("command", command);
        payload.put("conversations", conversations == null ? List.of() : conversations);
        try {
            return restClient.post()
                    .uri(properties.getBaseUrl() + properties.getDelegatedTaskCompilePath())
                    .body(payload)
                    .retrieve()
                    .body(DelegatedTaskCompilationResponse.class);
        } catch (RestClientException exception) {
            // Runtime 暂时不可用时保留本地解析器降级，不能让普通工作台命令一起失败。
            return null;
        }
    }

    /**
     * 按用户主动查看上下文的动作请求一次会话进度摘要。
     * 该接口绕开事件摄取链路，因此不会新增消息事件，也绝不会触发平台回写。
     */
    public JsonNode summarizeConversationProgress(
            String userId,
            String platform,
            String chatType,
            String chatId,
            List<ConversationMessageResponse> messages
    ) {
        if (!properties.isEnabled()) {
            return null;
        }

        Map<String, Object> payload = new LinkedHashMap<>();
        payload.put("userId", userId);
        payload.put("platform", platform == null ? "" : platform);
        payload.put("chatType", chatType == null ? "" : chatType);
        payload.put("chatId", chatId);
        payload.put("messages", messages);

        try {
            return restClient.post()
                    .uri(properties.getBaseUrl() + properties.getProgressPath())
                    .body(payload)
                    .retrieve()
                    .body(JsonNode.class);
        } catch (RestClientException exception) {
            // 摘要只是辅助信息，Runtime 暂时不可用时由应用服务生成本地概括并继续返回真实时间线。
            return null;
        }
    }

    /**
     * 在用户主动刷新认知卡时请求一次结构化会话分析。
     * 该调用只读取 Event Center 已校正身份的时间线，不创建事件，也不会触发平台回写。
     */
    public JsonNode analyzeConversationCognition(
            String userId,
            String platform,
            String chatType,
            String chatId,
            List<ConversationMessageResponse> messages
    ) {
        if (!properties.isEnabled()) {
            return null;
        }

        Map<String, Object> payload = new LinkedHashMap<>();
        payload.put("userId", userId);
        payload.put("platform", platform == null ? "" : platform);
        payload.put("chatType", chatType == null ? "" : chatType);
        payload.put("chatId", chatId);
        payload.put("messages", messages);

        try {
            return restClient.post()
                    .uri(properties.getBaseUrl() + properties.getCognitionPath())
                    .body(payload)
                    .retrieve()
                    .body(JsonNode.class);
        } catch (RestClientException exception) {
            // 认知分析失败时由应用层保留旧卡片，不能用空结果覆盖已经确认的用户信息。
            return null;
        }
    }

    /**
     * 读取某个事件对应的群管理审批摘要。Runtime 返回中不包含一次性审批令牌。
     */
    public JsonNode getPendingGroupOperation(String eventId) {
        String safeEventId = UriUtils.encodePathSegment(eventId, StandardCharsets.UTF_8);
        return restClient.get()
                .uri(properties.getBaseUrl() + properties.getPendingGroupOperationPath() + "/" + safeEventId)
                .retrieve()
                .body(JsonNode.class);
    }

    /**
     * 按事件提交确认短语。真实审批令牌始终留在 Runtime 内存中，不经过桌面客户端。
     */
    public JsonNode approveGroupOperation(String eventId, String confirmationText) {
        String safeEventId = UriUtils.encodePathSegment(eventId, StandardCharsets.UTF_8);
        return restClient.post()
                .uri(properties.getBaseUrl() + properties.getApproveGroupOperationPath() + "/" + safeEventId)
                .body(Map.of("confirmationText", confirmationText))
                .retrieve()
                .body(JsonNode.class);
    }
}
