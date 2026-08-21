package com.memoecho.eventcenter.service;

import com.fasterxml.jackson.databind.ObjectMapper;
import com.memoecho.eventcenter.dto.WorkspaceCommandRequest;
import com.memoecho.eventcenter.dto.WorkspaceCommandResponse;
import com.memoecho.eventcenter.dto.WorkspaceThreadMessageSendResponse;
import com.memoecho.eventcenter.dto.WorkspaceThreadMessageResponse;
import com.memoecho.eventcenter.dto.WorkspaceThreadResponse;
import com.memoecho.eventcenter.model.DelegatedWorkflow;
import com.memoecho.eventcenter.model.WorkspaceThread;
import com.memoecho.eventcenter.model.WorkspaceThreadMessage;
import com.memoecho.eventcenter.repository.JdbcDelegatedTaskRepository;
import com.memoecho.eventcenter.repository.JdbcDelegatedWorkflowRepository;
import com.memoecho.eventcenter.repository.JdbcWorkspaceThreadRepository;
import org.springframework.http.HttpStatus;
import org.springframework.stereotype.Service;
import org.springframework.web.server.ResponseStatusException;

import java.time.Instant;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;
import java.util.UUID;

/**
 * 主控台对话式工作区的线程与消息编排。
 *
 * 线程是对话容器，委托任务/父工作流独立运行。sendMessage 复用
 * {@link WorkspaceCommandApplicationService} 执行命令，并在返回后按
 * executionId（= commandId）反查该命令创建的任务与工作流，回填到 agent 消息，
 * 供前端把任务卡片内嵌进对话流。
 */
@Service
public class WorkspaceThreadApplicationService {

    private final JdbcWorkspaceThreadRepository threadRepository;
    private final JdbcDelegatedTaskRepository taskRepository;
    private final JdbcDelegatedWorkflowRepository workflowRepository;
    private final WorkspaceCommandApplicationService commandApplicationService;
    private final ObjectMapper objectMapper;

    public WorkspaceThreadApplicationService(
            JdbcWorkspaceThreadRepository threadRepository,
            JdbcDelegatedTaskRepository taskRepository,
            JdbcDelegatedWorkflowRepository workflowRepository,
            WorkspaceCommandApplicationService commandApplicationService,
            ObjectMapper objectMapper
    ) {
        this.threadRepository = threadRepository;
        this.taskRepository = taskRepository;
        this.workflowRepository = workflowRepository;
        this.commandApplicationService = commandApplicationService;
        this.objectMapper = objectMapper;
    }

    public WorkspaceThreadResponse createThread(String userId, String title) {
        Instant now = Instant.now();
        String normalizedTitle = title == null ? "" : title.strip();
        WorkspaceThread thread = new WorkspaceThread(
                UUID.randomUUID().toString(),
                userId,
                normalizedTitle,
                false,
                false,
                now,
                now
        );
        return WorkspaceThreadResponse.from(threadRepository.insertThread(thread));
    }

    public List<WorkspaceThreadResponse> listThreads(String userId, boolean includeArchived) {
        return threadRepository.listThreads(userId, includeArchived)
                .stream()
                .map(WorkspaceThreadResponse::from)
                .toList();
    }

    public WorkspaceThreadResponse updateThread(String userId, String threadId, String title, Boolean pinned, Boolean archived) {
        WorkspaceThread current = requireThread(userId, threadId);
        WorkspaceThread updated = new WorkspaceThread(
                current.id(),
                current.userId(),
                title == null ? current.title() : title.strip(),
                pinned == null ? current.pinned() : pinned,
                archived == null ? current.archived() : archived,
                current.createdAt(),
                Instant.now()
        );
        return WorkspaceThreadResponse.from(threadRepository.updateThread(updated));
    }

    public List<WorkspaceThreadMessageResponse> listMessages(String userId, String threadId, int limit, String before) {
        requireThread(userId, threadId);
        int safeLimit = Math.max(1, Math.min(limit, 200));
        Instant beforeInstant = before == null || before.isBlank() ? null : Instant.parse(before);
        return threadRepository.listMessages(threadId, safeLimit, beforeInstant)
                .stream()
                .map(WorkspaceThreadMessageResponse::from)
                .toList();
    }

    public WorkspaceThreadMessageResponse getMessage(String userId, String threadId, String messageId) {
        requireThread(userId, threadId);
        return threadRepository.findMessageByIdAndUserId(messageId, userId)
                .map(WorkspaceThreadMessageResponse::from)
                .orElseThrow(() -> new ResponseStatusException(HttpStatus.NOT_FOUND, "消息不存在"));
    }

    public WorkspaceThreadMessageSendResponse sendMessage(String userId, String threadId, String content) {
        WorkspaceThread thread = requireThread(userId, threadId);
        String normalizedContent = content == null ? "" : content.strip();
        if (normalizedContent.isEmpty()) {
            throw new ResponseStatusException(HttpStatus.BAD_REQUEST, "消息内容不能为空");
        }
        Instant now = Instant.now();
        WorkspaceThreadMessage userMessage = threadRepository.insertMessage(new WorkspaceThreadMessage(
                UUID.randomUUID().toString(),
                thread.id(),
                userId,
                "user",
                normalizedContent,
                "done",
                "",
                null,
                null,
                null,
                now
        ));

        WorkspaceCommandResponse response;
        String errorText = "";
        try {
            response = commandApplicationService.execute(userId, new WorkspaceCommandRequest(normalizedContent, null));
        } catch (Exception exception) {
            errorText = String.valueOf(exception.getMessage() == null ? exception.getClass().getSimpleName() : exception.getMessage());
            response = null;
        }
        if (response == null) {
            WorkspaceThreadMessage failed = threadRepository.insertMessage(new WorkspaceThreadMessage(
                    UUID.randomUUID().toString(),
                    thread.id(),
                    userId,
                    "agent",
                    errorText.isBlank() ? "命令执行失败" : errorText,
                    "error",
                    "",
                    null,
                    null,
                    resultOf(null, List.of(), List.of(), errorText),
                    Instant.now()
            ));
            threadRepository.touchThread(thread.id(), userId, Instant.now());
            WorkspaceCommandResponse fallback = new WorkspaceCommandResponse("", "failed", "", "", errorText, false, List.of(), null, errorText);
            return new WorkspaceThreadMessageSendResponse(
                    WorkspaceThreadMessageResponse.from(userMessage),
                    WorkspaceThreadMessageResponse.from(failed),
                    fallback
            );
        }

        String executionId = response.commandId() == null ? "" : response.commandId();
        List<String> taskIds = executionId.isBlank()
                ? List.of()
                : taskRepository.findTaskIdsBySourceExecutionId(userId, executionId);
        String workflowId = null;
        if (!executionId.isBlank()) {
            workflowId = workflowRepository.findBySourceExecutionIdAndUserId(executionId, userId)
                    .map(DelegatedWorkflow::id)
                    .orElse(null);
        }
        String status = mapMessageStatus(response);
        String reply = response.finalReply() == null || response.finalReply().isBlank()
                ? (response.summary() == null ? "" : response.summary())
                : response.finalReply();
        WorkspaceThreadMessage agentMessage = threadRepository.insertMessage(new WorkspaceThreadMessage(
                UUID.randomUUID().toString(),
                thread.id(),
                userId,
                "agent",
                reply,
                status,
                executionId,
                taskIds.isEmpty() ? null : taskIds.get(0),
                workflowId,
                resultOf(response, taskIds, workflowId == null ? List.of() : List.of(workflowId), errorText),
                Instant.now()
        ));
        threadRepository.touchThread(thread.id(), userId, Instant.now());
        return new WorkspaceThreadMessageSendResponse(
                WorkspaceThreadMessageResponse.from(userMessage),
                WorkspaceThreadMessageResponse.from(agentMessage),
                response
        );
    }

    private WorkspaceThread requireThread(String userId, String threadId) {
        return threadRepository.findThreadByIdAndUserId(threadId, userId)
                .orElseThrow(() -> new ResponseStatusException(HttpStatus.NOT_FOUND, "会话不存在"));
    }

    private static String mapMessageStatus(WorkspaceCommandResponse response) {
        if ("failed".equalsIgnoreCase(response.status())) {
            return "error";
        }
        if (response.needConfirmation()) {
            return "needs_confirmation";
        }
        return "done";
    }

    private String resultOf(
            WorkspaceCommandResponse response,
            List<String> taskIds,
            List<String> workflowIds,
            String errorText
    ) {
        Map<String, Object> result = new LinkedHashMap<>();
        if (response != null) {
            result.put("response", response);
        }
        result.put("taskIds", taskIds);
        result.put("workflowIds", workflowIds);
        if (!errorText.isBlank()) {
            result.put("error", errorText);
        }
        try {
            return objectMapper.writeValueAsString(result);
        } catch (Exception exception) {
            return "{\"error\":\"result serialization failed\"}";
        }
    }
}