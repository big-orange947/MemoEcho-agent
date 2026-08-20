package com.memoecho.eventcenter.dto;

import com.fasterxml.jackson.core.type.TypeReference;
import com.fasterxml.jackson.databind.ObjectMapper;
import com.memoecho.eventcenter.model.DelegatedTask;

import java.util.List;

/** 客户端可见的委托任务摘要，不暴露后续执行器的内部状态或模型上下文。 */
public record DelegatedTaskResponse(
        String id,
        String taskType,
        String status,
        String originalCommand,
        String targetQuery,
        String platform,
        String chatType,
        String chatId,
        String targetName,
        String objective,
        String successCriteria,
        String deadlineText,
        double confidence,
        String clarificationQuestion,
        boolean requiresConfirmation,
        String executionMode,
        String progressSummary,
        String stateJson,
        String lastEventId,
        String startedAt,
        String completedAt,
        String completionReport,
        String createdAt,
        String updatedAt,
        String workflowId,
        String stepKey,
        String stepRole,
        String startEventId,
        String conversationScopeJson,
        List<String> producesFacts
) {
    private static final ObjectMapper OBJECT_MAPPER = new ObjectMapper();
    private static final TypeReference<List<String>> STRING_LIST_TYPE = new TypeReference<>() {
    };

    /** 将领域对象转换成稳定的 API 响应。 */
    public static DelegatedTaskResponse from(DelegatedTask task) {
        return new DelegatedTaskResponse(
                task.id(), task.taskType(), task.status(), task.originalCommand(), task.targetQuery(),
                task.platform(), task.chatType(), task.chatId(), task.targetName(), task.objective(),
                task.successCriteria(), task.deadlineText(), task.confidence(), task.clarificationQuestion(),
                task.requiresConfirmation(), task.executionMode(), task.progressSummary(), task.stateJson(),
                task.lastEventId(), task.startedAt() == null ? null : task.startedAt().toString(),
                task.completedAt() == null ? null : task.completedAt().toString(), task.completionReport(),
                task.createdAt().toString(), task.updatedAt().toString(), task.workflowId(), task.stepKey(),
                task.stepRole(), task.startEventId(), task.conversationScopeJson(),
                readStringList(task.producesFactsJson())
        );
    }

    /** 解析步骤声明的事实键；旧数据为空或损坏时返回空列表，避免任务查询接口整体失败。 */
    private static List<String> readStringList(String json) {
        if (json == null || json.isBlank()) {
            return List.of();
        }
        try {
            return List.copyOf(OBJECT_MAPPER.readValue(json, STRING_LIST_TYPE));
        } catch (Exception ignored) {
            return List.of();
        }
    }
}
