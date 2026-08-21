package com.memoecho.eventcenter.service;

import com.fasterxml.jackson.databind.ObjectMapper;
import com.memoecho.eventcenter.dto.WorkspaceCommandRequest;
import com.memoecho.eventcenter.dto.WorkspaceCommandResponse;
import com.memoecho.eventcenter.dto.WorkspaceThreadMessageSendResponse;
import com.memoecho.eventcenter.dto.WorkspaceThreadMessageResponse;
import com.memoecho.eventcenter.dto.WorkspaceThreadResponse;
import com.memoecho.eventcenter.model.DelegatedTask;
import com.memoecho.eventcenter.model.DelegatedWorkflow;
import com.memoecho.eventcenter.model.WorkspaceThread;
import com.memoecho.eventcenter.model.WorkspaceThreadMessage;
import com.memoecho.eventcenter.repository.JdbcDelegatedTaskRepository;
import com.memoecho.eventcenter.repository.JdbcDelegatedWorkflowRepository;
import com.memoecho.eventcenter.repository.JdbcWorkspaceThreadRepository;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.http.HttpStatus;
import org.springframework.stereotype.Service;
import org.springframework.web.server.ResponseStatusException;
import org.springframework.web.servlet.mvc.method.annotation.SseEmitter;

import java.io.IOException;
import java.time.Duration;
import java.time.Instant;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;
import java.util.Optional;
import java.util.UUID;
import java.util.concurrent.ConcurrentHashMap;
import java.util.concurrent.ExecutorService;
import java.util.concurrent.Executors;
import java.util.concurrent.ThreadFactory;
import java.util.concurrent.atomic.AtomicInteger;

/**
 * 主控台对话式工作区的线程与消息编排（P2：异步 + SSE 流式）。
 *
 * sendMessage 立即返回（202 语义）：先落库 user 消息与 status=streaming 的 agent
 * 消息，然后在后台线程执行命令；执行期间按 commandId 轮询委托任务/父工作流进度，
 * 通过 {@link #streamMessage} 建立的 SSE 连接推送 stage 事件（任务创建、状态变化），
 * 命令结束后回写 agent 消息终态并推送 done/error 事件。
 */
@Service
public class WorkspaceThreadApplicationService {

    private static final Logger log = LoggerFactory.getLogger(WorkspaceThreadApplicationService.class);

    /** streaming 消息超过该时限仍未完成时视为执行超时。 */
    private static final Duration STALE_STREAMING_TIMEOUT = Duration.ofMinutes(15);

    private final JdbcWorkspaceThreadRepository threadRepository;
    private final JdbcDelegatedTaskRepository taskRepository;
    private final JdbcDelegatedWorkflowRepository workflowRepository;
    private final WorkspaceCommandApplicationService commandApplicationService;
    private final ObjectMapper objectMapper;

    private final ConcurrentHashMap<String, SseEmitter> messageEmitters = new ConcurrentHashMap<>();

    private static final ExecutorService COMMAND_EXECUTOR = Executors.newFixedThreadPool(
            2,
            new ThreadFactory() {
                private final AtomicInteger counter = new AtomicInteger(1);

                @Override
                public Thread newThread(Runnable runnable) {
                    Thread thread = new Thread(runnable, "ws-thread-command-" + counter.getAndIncrement());
                    thread.setDaemon(true);
                    return thread;
                }
            }
    );

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
        List<WorkspaceThreadMessage> messages = threadRepository.listMessages(threadId, safeLimit, beforeInstant);
        // 超时未完成的 streaming 消息自动标记为 error，避免刷新后永久悬挂。
        Instant staleBefore = Instant.now().minus(STALE_STREAMING_TIMEOUT);
        for (WorkspaceThreadMessage message : messages) {
            if ("streaming".equals(message.status())
                    && message.createdAt() != null
                    && message.createdAt().isBefore(staleBefore)) {
                threadRepository.updateMessageStatus(message.id(), userId, "error", "执行超时或服务中断，未能完成本次命令");
            }
        }
        return messages.stream().map(WorkspaceThreadMessageResponse::from).toList();
    }

    public WorkspaceThreadMessageResponse getMessage(String userId, String threadId, String messageId) {
        requireThread(userId, threadId);
        return threadRepository.findMessageByIdAndUserId(messageId, userId)
                .map(WorkspaceThreadMessageResponse::from)
                .orElseThrow(() -> new ResponseStatusException(HttpStatus.NOT_FOUND, "消息不存在"));
    }

    /**
     * 发送消息：立即落库 user + streaming agent 消息并返回，命令在后台线程执行。
     * 进度通过 {@link #streamMessage} 的 SSE 推送。
     */
    public WorkspaceThreadMessageSendResponse sendMessage(String userId, String threadId, String content) {
        WorkspaceThread thread = requireThread(userId, threadId);
        String normalizedContent = content == null ? "" : content.strip();
        if (normalizedContent.isEmpty()) {
            throw new ResponseStatusException(HttpStatus.BAD_REQUEST, "消息内容不能为空");
        }
        Instant now = Instant.now();
        String commandId = "desktop:command:" + UUID.randomUUID();
        WorkspaceThreadMessage userMessage = threadRepository.insertMessage(new WorkspaceThreadMessage(
                UUID.randomUUID().toString(),
                thread.id(),
                userId,
                "user",
                normalizedContent,
                "done",
                commandId,
                null,
                null,
                null,
                now
        ));
        WorkspaceThreadMessage agentMessage = threadRepository.insertMessage(new WorkspaceThreadMessage(
                UUID.randomUUID().toString(),
                thread.id(),
                userId,
                "agent",
                "",
                "streaming",
                commandId,
                null,
                null,
                null,
                now
        ));
        threadRepository.touchThread(thread.id(), userId, now);
        COMMAND_EXECUTOR.submit(() -> runMessageCommand(userId, thread.id(), userMessage.id(), agentMessage.id(), commandId, normalizedContent));
        return new WorkspaceThreadMessageSendResponse(
                WorkspaceThreadMessageResponse.from(userMessage),
                WorkspaceThreadMessageResponse.from(agentMessage),
                commandId
        );
    }

    /**
     * 订阅单条 agent 消息的执行进度 SSE。连接建立后先发当前快照，再推送实时 stage 事件。
     */
    public SseEmitter streamMessage(String userId, String threadId, String messageId) {
        requireThread(userId, threadId);
        WorkspaceThreadMessage message = threadRepository.findMessageByIdAndUserId(messageId, userId)
                .orElseThrow(() -> new ResponseStatusException(HttpStatus.NOT_FOUND, "消息不存在"));
        SseEmitter emitter = new SseEmitter(0L);
        emitter.onCompletion(() -> messageEmitters.remove(messageId, emitter));
        emitter.onTimeout(() -> messageEmitters.remove(messageId, emitter));
        messageEmitters.put(messageId, emitter);
        try {
            emitter.send(SseEmitter.event().name("connected").data(snapshotOf(message)));
        } catch (IOException exception) {
            emitter.completeWithError(exception);
            return emitter;
        }
        // streaming 期间由独立轮询线程按 commandId 推送任务/工作流进度（P2 阶段事件）。
        if ("streaming".equals(message.status())) {
            startProgressPoller(emitter, userId, messageId, message.executionId());
        }
        return emitter;
    }

    /** 轮询委托任务/父工作流进度并推送 stage 事件；消息离开 streaming 态或连接关闭后停止。 */
    private void startProgressPoller(SseEmitter emitter, String userId, String messageId, String executionId) {
        Thread poller = new Thread(() -> {
            String snapshotKey = "";
            while (messageEmitters.get(messageId) == emitter) {
                WorkspaceThreadMessage current = threadRepository.findMessageByIdAndUserId(messageId, userId).orElse(null);
                if (current == null || !"streaming".equals(current.status())) {
                    return;
                }
                if (executionId != null && !executionId.isBlank()) {
                    try {
                        List<DelegatedTask> tasks = taskRepository.findBySourceExecutionId(userId, executionId);
                        Optional<DelegatedWorkflow> workflow =
                                workflowRepository.findBySourceExecutionIdAndUserId(executionId, userId);
                        String key = buildProgressKey(tasks, workflow);
                        if (!key.equals(snapshotKey)) {
                            snapshotKey = key;
                            emitStage(messageId, "progress", progressPayload(tasks, workflow));
                        }
                    } catch (Exception exception) {
                        log.warn("消息进度轮询失败：messageId={}, error={}", messageId, exception.getMessage());
                    }
                }
                try {
                    Thread.sleep(1500);
                } catch (InterruptedException interrupted) {
                    Thread.currentThread().interrupt();
                    return;
                }
            }
        }, "ws-thread-progress-" + messageId.substring(0, Math.min(8, messageId.length())));
        poller.setDaemon(true);
        poller.start();
    }

    private static String buildProgressKey(List<DelegatedTask> tasks, Optional<DelegatedWorkflow> workflow) {
        StringBuilder key = new StringBuilder();
        for (DelegatedTask task : tasks) {
            key.append(task.id()).append(':').append(task.status()).append(':')
                    .append(task.progressSummary() == null ? "" : task.progressSummary()).append(';');
        }
        workflow.ifPresent(item -> key.append("wf:").append(item.id()).append(':').append(item.status()).append(';'));
        return key.toString();
    }

    private Map<String, Object> progressPayload(List<DelegatedTask> tasks, Optional<DelegatedWorkflow> workflow) {
        Map<String, Object> payload = new LinkedHashMap<>();
        payload.put("tasks", tasks.stream().map(task -> {
            Map<String, Object> view = new LinkedHashMap<>();
            view.put("id", task.id());
            view.put("status", task.status());
            view.put("stepKey", task.stepKey() == null ? "" : task.stepKey());
            view.put("objective", task.objective() == null ? "" : task.objective());
            view.put("progressSummary", task.progressSummary() == null ? "" : task.progressSummary());
            view.put("workflowId", task.workflowId() == null ? "" : task.workflowId());
            return view;
        }).toList());
        workflow.ifPresent(item -> {
            Map<String, Object> view = new LinkedHashMap<>();
            view.put("id", item.id());
            view.put("status", item.status());
            view.put("progressSummary", item.progressSummary() == null ? "" : item.progressSummary());
            payload.put("workflow", view);
        });
        return payload;
    }

    /** 后台执行命令：轮询任务/工作流进度并推送事件，结束后回写终态。 */
    void runMessageCommand(
            String userId,
            String threadId,
            String userMessageId,
            String agentMessageId,
            String commandId,
            String content
    ) {
        WorkspaceCommandResponse response = null;
        String errorText = "";
        try {
            emitStage(agentMessageId, "processing", Map.of("message", "已受理，正在编译任务…"));
            response = commandApplicationService.execute(userId, new WorkspaceCommandRequest(content, null), commandId);
            // 命令返回时 runtime 已同步完成本次动作，任务/工作流已在库中，无需长时间轮询。
        } catch (Exception exception) {
            errorText = String.valueOf(
                    exception.getMessage() == null ? exception.getClass().getSimpleName() : exception.getMessage());
        }
        try {
            WorkspaceThreadMessage finalMessage = null;
            WorkspaceThreadMessage currentMessage = threadRepository
                    .findMessageByIdAndUserId(agentMessageId, userId)
                    .orElse(null);
            if (response == null) {
                String failureText = errorText.isBlank() ? "命令执行失败" : errorText;
                if (currentMessage != null) {
                    finalMessage = new WorkspaceThreadMessage(
                            currentMessage.id(), currentMessage.threadId(), currentMessage.userId(), "agent",
                            failureText, "error", currentMessage.executionId(),
                            currentMessage.taskId(), currentMessage.workflowId(),
                            resultOf(null, List.of(), List.of(), errorText),
                            currentMessage.createdAt());
                }
            } else {
                List<DelegatedTask> tasks = commandId.isBlank()
                        ? List.of()
                        : taskRepository.findBySourceExecutionId(userId, commandId);
                List<String> taskIds = tasks.stream().map(DelegatedTask::id).toList();
                String workflowId = null;
                if (!commandId.isBlank()) {
                    workflowId = workflowRepository.findBySourceExecutionIdAndUserId(commandId, userId)
                            .map(DelegatedWorkflow::id)
                            .orElse(null);
                }
                String status = mapMessageStatus(response);
                String reply = response.finalReply() == null || response.finalReply().isBlank()
                        ? (response.summary() == null ? "" : response.summary())
                        : response.finalReply();
                List<String> workflowIds = workflowId == null ? List.of() : List.of(workflowId);
                if (currentMessage != null) {
                    finalMessage = new WorkspaceThreadMessage(
                            currentMessage.id(), currentMessage.threadId(), currentMessage.userId(), "agent",
                            reply, status, commandId,
                            taskIds.isEmpty() ? null : taskIds.get(0), workflowId,
                            resultOf(response, taskIds, workflowIds, errorText),
                            currentMessage.createdAt());
                }
            }
            if (finalMessage != null) {
                threadRepository.updateMessage(finalMessage);
                emitStage(agentMessageId, "done",
                        Map.of("agentMessage", WorkspaceThreadMessageResponse.from(finalMessage)));
            }
        } catch (Exception exception) {
            log.warn("命令执行结果回写失败：threadId={}, commandId={}, error={}",
                    threadId, commandId, exception.getMessage());
            emitStage(agentMessageId, "error", Map.of("message", "结果回写失败：" + exception.getMessage()));
        } finally {
            threadRepository.touchThread(threadId, userId, Instant.now());
            SseEmitter emitter = messageEmitters.remove(agentMessageId);
            if (emitter != null) {
                emitter.complete();
            }
        }
    }

    private void emitStage(String agentMessageId, String stage, Map<String, Object> payload) {
        SseEmitter emitter = messageEmitters.get(agentMessageId);
        if (emitter == null) {
            return;
        }
        Map<String, Object> event = new LinkedHashMap<>();
        event.put("stage", stage);
        event.putAll(payload);
        try {
            emitter.send(SseEmitter.event().name("stage").data(toJson(event)));
        } catch (IOException exception) {
            messageEmitters.remove(agentMessageId, emitter);
        }
    }

    private Object snapshotOf(WorkspaceThreadMessage message) {
        return Map.of(
                "messageId", message.id(),
                "status", message.status(),
                "executionId", message.executionId() == null ? "" : message.executionId()
        );
    }

    private String toJson(Object value) {
        try {
            return objectMapper.writeValueAsString(value);
        } catch (Exception exception) {
            return "{\"stage\":\"raw\",\"value\":\"" + String.valueOf(value).replace("\"", "'") + "\"}";
        }
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
        return toJson(result);
    }
}