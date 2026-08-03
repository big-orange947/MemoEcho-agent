package com.memoecho.eventcenter.service;

import com.fasterxml.jackson.databind.JsonNode;
import com.fasterxml.jackson.databind.ObjectMapper;
import com.fasterxml.jackson.databind.node.ObjectNode;
import com.memoecho.eventcenter.dto.DispatchResult;
import com.memoecho.eventcenter.dto.EventIngestResponse;
import com.memoecho.eventcenter.dto.SenderPayload;
import com.memoecho.eventcenter.dto.UnifiedEventPayload;
import com.memoecho.eventcenter.dto.WorkspaceCommandAgentResponse;
import com.memoecho.eventcenter.dto.WorkspaceCommandRequest;
import com.memoecho.eventcenter.dto.WorkspaceCommandResponse;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.http.HttpStatus;
import org.springframework.stereotype.Service;
import org.springframework.web.server.ResponseStatusException;

import java.time.Instant;
import java.util.ArrayList;
import java.util.List;
import java.util.Set;
import java.util.UUID;

@Service
public class WorkspaceCommandApplicationService {

    private static final Logger log = LoggerFactory.getLogger(WorkspaceCommandApplicationService.class);

    private static final Set<String> ALLOWED_ROUTES = Set.of(
            "social_reply",
            "chat_summary",
            "task_plan",
            "schedule_extract",
            "file_analysis",
            "message_dispatch",
            "group_ops"
    );

    private final EventCenterApplicationService eventCenterApplicationService;
    private final ObjectMapper objectMapper;

    /**
     * 注入事件主流程和 JSON 构造器，桌面命令继续复用事件持久化与 Runtime 派发能力。
     */
    public WorkspaceCommandApplicationService(
            EventCenterApplicationService eventCenterApplicationService,
            ObjectMapper objectMapper
    ) {
        this.eventCenterApplicationService = eventCenterApplicationService;
        this.objectMapper = objectMapper;
    }

    /**
     * 把当前用户的桌面指令转换成不会回写聊天平台的标准事件，并返回适合 UI 展示的执行结果。
     */
    public WorkspaceCommandResponse execute(String userId, WorkspaceCommandRequest request) {
        String commandId = "desktop:command:" + UUID.randomUUID();
        String requestedRoute = normalizeRoute(request.requestedRoute());
        // commandId 同时是跨 Java、Python 和任务状态回写的 executionId，便于一次命令的全链路检索。
        log.info("主控台委托闭环 | executionId={} | stage=command_received | userId={} | requestedRoute={} | promptLength={}",
                commandId,
                userId,
                requestedRoute == null ? "auto" : requestedRoute,
                request.prompt().trim().length());
        ObjectNode rawPayload = objectMapper.createObjectNode();
        rawPayload.put("source", "desktop-client");
        rawPayload.put("userId", userId);
        rawPayload.put("commandId", commandId);
        rawPayload.put("executionId", commandId);
        // 主控台命令统一交给 Python Runtime/LangGraph 编译，Java 不再用关键词规则提前创建任务。
        rawPayload.put("allowTaskCreation", true);
        if (requestedRoute != null) {
            rawPayload.put("requestedRoute", requestedRoute);
        }

        UnifiedEventPayload event = new UnifiedEventPayload(
                commandId,
                "desktop",
                "workspace",
                "desktop_command",
                "private",
                "workspace:" + userId,
                userId,
                new SenderPayload(userId, "desktop-user", "owner"),
                request.prompt().trim(),
                List.of(),
                List.of(),
                Instant.now().toString(),
                rawPayload
        );

        EventIngestResponse ingestResponse = eventCenterApplicationService.ingest(event);
        DispatchResult dispatch = ingestResponse.dispatch();
        log.info("主控台委托闭环 | executionId={} | stage=event_dispatched | eventId={} | accepted={} | duplicate={} | attempted={} | httpStatus={} | hasDispatchError={}",
                commandId,
                ingestResponse.eventId(),
                ingestResponse.accepted(),
                ingestResponse.duplicate(),
                dispatch != null && dispatch.attempted(),
                dispatch == null ? null : dispatch.httpStatus(),
                dispatch != null && dispatch.error() != null && !dispatch.error().isBlank());
        return toResponse(commandId, dispatch);
    }

    /**
     * 校验前端显式路由，只允许进入已注册的 Agent 工作流，避免用户通过参数调用任意内部实现。
     */
    private String normalizeRoute(String requestedRoute) {
        if (requestedRoute == null || requestedRoute.isBlank()) {
            return null;
        }
        String normalized = requestedRoute.trim().toLowerCase();
        if (!ALLOWED_ROUTES.contains(normalized)) {
            throw new ResponseStatusException(HttpStatus.BAD_REQUEST, "不支持的 Agent 路由：" + normalized);
        }
        return normalized;
    }

    /**
     * 将 Runtime 派发结果压缩成客户端稳定契约，并保留失败原因供界面直接反馈。
     */
    private WorkspaceCommandResponse toResponse(String commandId, DispatchResult dispatch) {
        if (dispatch == null || !dispatch.attempted()) {
            return failed(commandId, "Agent Runtime 当前未启用。");
        }
        if (dispatch.error() != null && !dispatch.error().isBlank()) {
            return failed(commandId, dispatch.error());
        }
        JsonNode body = dispatch.body();
        if (body == null || body.isNull()) {
            return failed(commandId, "Agent Runtime 未返回执行结果。");
        }

        List<WorkspaceCommandAgentResponse> results = readAgentResults(body.path("results"));
        boolean needConfirmation = body.path("results").isArray()
                && resultsNeedConfirmation(body.path("results"));
        String finalReply = body.path("final_reply").asText("").trim();
        if ("No reply was generated.".equals(finalReply)) {
            finalReply = "";
        }
        return new WorkspaceCommandResponse(
                commandId,
                body.path("status").asText("success"),
                body.path("route").asText(""),
                body.path("summary").asText(""),
                finalReply,
                needConfirmation,
                results,
                null,
                ""
        );
    }

    /**
     * 读取各 Agent 的公开字段，忽略工具内部参数和模型上下文等不应暴露给 UI 的信息。
     */
    private List<WorkspaceCommandAgentResponse> readAgentResults(JsonNode resultNodes) {
        List<WorkspaceCommandAgentResponse> results = new ArrayList<>();
        if (!resultNodes.isArray()) {
            return results;
        }
        for (JsonNode result : resultNodes) {
            results.add(new WorkspaceCommandAgentResponse(
                    result.path("agent").asText("unknown"),
                    result.path("status").asText("unknown"),
                    result.path("reply_draft").asText(""),
                    readTextArray(result.path("next_actions"))
            ));
        }
        return results;
    }

    /**
     * 将 JSON 文本数组安全转换成 Java 列表，异常字段会被忽略而不是影响整个命令响应。
     */
    private List<String> readTextArray(JsonNode node) {
        List<String> values = new ArrayList<>();
        if (!node.isArray()) {
            return values;
        }
        node.forEach(value -> {
            if (value.isTextual()) {
                values.add(value.asText());
            }
        });
        return values;
    }

    /**
     * 汇总各 Agent 的人工确认信号，供客户端显示风险提示。
     */
    private boolean resultsNeedConfirmation(JsonNode resultNodes) {
        for (JsonNode result : resultNodes) {
            if (result.path("need_confirmation").asBoolean(false)) {
                return true;
            }
        }
        return false;
    }

    /**
     * 构造可直接展示的失败响应，命令事件仍保留在事件中心供后续诊断和重试。
     */
    private WorkspaceCommandResponse failed(String commandId, String error) {
        return new WorkspaceCommandResponse(
                commandId,
                "failed",
                "",
                "",
                "",
                false,
                List.of(),
                null,
                error == null ? "未知错误" : error
        );
    }
}
