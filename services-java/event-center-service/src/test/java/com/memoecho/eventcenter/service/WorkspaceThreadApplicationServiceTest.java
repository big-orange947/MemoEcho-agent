package com.memoecho.eventcenter.service;

import com.fasterxml.jackson.databind.ObjectMapper;
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
import org.junit.jupiter.api.Test;
import org.springframework.web.server.ResponseStatusException;

import java.time.Instant;
import java.util.List;
import java.util.Optional;

import static org.assertj.core.api.Assertions.assertThat;
import static org.assertj.core.api.Assertions.assertThatThrownBy;
import static org.mockito.ArgumentMatchers.any;
import static org.mockito.ArgumentMatchers.eq;
import static org.mockito.Mockito.mock;
import static org.mockito.Mockito.verify;
import static org.mockito.Mockito.when;

class WorkspaceThreadApplicationServiceTest {

    private static final String USER_ID = "user-1";
    private static final String THREAD_ID = "thread-1";

    private final JdbcWorkspaceThreadRepository threadRepository = mock(JdbcWorkspaceThreadRepository.class);
    private final JdbcDelegatedTaskRepository taskRepository = mock(JdbcDelegatedTaskRepository.class);
    private final JdbcDelegatedWorkflowRepository workflowRepository = mock(JdbcDelegatedWorkflowRepository.class);
    private final WorkspaceCommandApplicationService commandService = mock(WorkspaceCommandApplicationService.class);
    private final ObjectMapper objectMapper = new ObjectMapper().findAndRegisterModules();

    private final WorkspaceThreadApplicationService service = new WorkspaceThreadApplicationService(
            threadRepository, taskRepository, workflowRepository, commandService, objectMapper);

    private WorkspaceThread thread() {
        Instant now = Instant.parse("2026-08-21T10:00:00Z");
        return new WorkspaceThread(THREAD_ID, USER_ID, "约游戏", false, false, now, now);
    }

    @Test
    void shouldCreateThreadWithTitle() {
        when(threadRepository.insertThread(any(WorkspaceThread.class)))
                .thenAnswer(invocation -> invocation.getArgument(0));

        WorkspaceThreadResponse created = service.createThread(USER_ID, " 约游戏 ");

        assertThat(created.id()).isNotBlank();
        assertThat(created.userId()).isEqualTo(USER_ID);
        assertThat(created.title()).isEqualTo("约游戏");
        assertThat(created.archived()).isFalse();
    }

    @Test
    void shouldRejectMessagesFromOtherUsersThread() {
        when(threadRepository.findThreadByIdAndUserId(THREAD_ID, USER_ID))
                .thenReturn(Optional.empty());

        assertThatThrownBy(() -> service.sendMessage(USER_ID, THREAD_ID, "你好"))
                .isInstanceOf(ResponseStatusException.class)
                .hasMessageContaining("404");
    }

    @Test
    void shouldBackfillTaskAndWorkflowIdsAfterCommandExecution() {
        when(threadRepository.findThreadByIdAndUserId(THREAD_ID, USER_ID))
                .thenReturn(Optional.of(thread()));
        when(threadRepository.insertMessage(any(WorkspaceThreadMessage.class)))
                .thenAnswer(invocation -> invocation.getArgument(0));
        when(taskRepository.findTaskIdsBySourceExecutionId(USER_ID, "cmd-1"))
                .thenReturn(List.of("task-a", "task-b"));
        when(workflowRepository.findBySourceExecutionIdAndUserId("cmd-1", USER_ID))
                .thenReturn(Optional.of(new DelegatedWorkflow(
                        "wf-1", USER_ID, "cmd-1", "原命令", "标题", "PLAN_EXECUTE", "RUNNING",
                        "{}", "{}", "", "", null, null, null)));
        when(commandService.execute(eq(USER_ID), any()))
                .thenReturn(new WorkspaceCommandResponse(
                        "cmd-1", "success", "delegated_task", "已创建 2 步骤工作流",
                        "委托任务已创建", false, List.of(), null, ""));

        WorkspaceThreadMessageSendResponse result = service.sendMessage(USER_ID, THREAD_ID, "帮我和 km 约时间");

        assertThat(result.userMessage().role()).isEqualTo("user");
        assertThat(result.userMessage().content()).isEqualTo("帮我和 km 约时间");
        assertThat(result.agentMessage().role()).isEqualTo("agent");
        assertThat(result.agentMessage().status()).isEqualTo("done");
        assertThat(result.agentMessage().content()).contains("委托任务已创建");
        assertThat(result.agentMessage().executionId()).isEqualTo("cmd-1");
        // 多任务命令回填第一个任务 ID，工作流 ID 单独回填。
        assertThat(result.agentMessage().taskId()).isEqualTo("task-a");
        assertThat(result.agentMessage().workflowId()).isEqualTo("wf-1");
        assertThat(result.agentMessage().resultJson()).isNotBlank();
        assertThat(result.response().commandId()).isEqualTo("cmd-1");
        verify(threadRepository).touchThread(eq(THREAD_ID), eq(USER_ID), any());
    }

    @Test
    void shouldRecordErrorMessageWhenCommandFails() {
        when(threadRepository.findThreadByIdAndUserId(THREAD_ID, USER_ID))
                .thenReturn(Optional.of(thread()));
        when(threadRepository.insertMessage(any(WorkspaceThreadMessage.class)))
                .thenAnswer(invocation -> invocation.getArgument(0));
        when(commandService.execute(eq(USER_ID), any()))
                .thenThrow(new IllegalStateException("runtime timeout"));

        WorkspaceThreadMessageSendResponse result = service.sendMessage(USER_ID, THREAD_ID, "做点什么");

        assertThat(result.agentMessage().status()).isEqualTo("error");
        assertThat(result.agentMessage().content()).contains("runtime timeout");
        // 用户消息仍然落库，命令失败不阻断对话。
        assertThat(result.userMessage().role()).isEqualTo("user");
    }

    @Test
    void shouldUpdateOnlyProvidedFields() {
        Instant now = Instant.parse("2026-08-21T10:00:00Z");
        when(threadRepository.findThreadByIdAndUserId(THREAD_ID, USER_ID))
                .thenReturn(Optional.of(thread()));
        when(threadRepository.updateThread(any(WorkspaceThread.class)))
                .thenAnswer(invocation -> invocation.getArgument(0));

        WorkspaceThreadResponse updated = service.updateThread(USER_ID, THREAD_ID, " 新标题 ", null, true);

        assertThat(updated.title()).isEqualTo("新标题");
        assertThat(updated.archived()).isTrue();
        assertThat(updated.pinned()).isFalse();
    }

    @Test
    void shouldRequireNonBlankMessageContent() {
        when(threadRepository.findThreadByIdAndUserId(THREAD_ID, USER_ID))
                .thenReturn(Optional.of(thread()));

        assertThatThrownBy(() -> service.sendMessage(USER_ID, THREAD_ID, "   "))
                .isInstanceOf(ResponseStatusException.class)
                .hasMessageContaining("400");
    }
}