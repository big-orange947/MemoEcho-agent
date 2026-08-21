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
import static org.mockito.ArgumentMatchers.anyString;
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
    void shouldReturnImmediatelyWithStreamingAgentMessage() {
        when(threadRepository.findThreadByIdAndUserId(THREAD_ID, USER_ID))
                .thenReturn(Optional.of(thread()));
        when(threadRepository.insertMessage(any(WorkspaceThreadMessage.class)))
                .thenAnswer(invocation -> invocation.getArgument(0));

        WorkspaceThreadMessageSendResponse result = service.sendMessage(USER_ID, THREAD_ID, "帮我和 km 约时间");

        // P2 异步语义：立即返回 user + streaming agent 消息和预生成 commandId。
        assertThat(result.userMessage().role()).isEqualTo("user");
        assertThat(result.userMessage().content()).isEqualTo("帮我和 km 约时间");
        assertThat(result.agentMessage().role()).isEqualTo("agent");
        assertThat(result.agentMessage().status()).isEqualTo("streaming");
        assertThat(result.agentMessage().executionId()).startsWith("desktop:command:");
        assertThat(result.commandId()).isEqualTo(result.agentMessage().executionId());
        // 异步执行线程结束后也会 touchThread，因此用至少一次断言。
        verify(threadRepository, org.mockito.Mockito.atLeastOnce()).touchThread(eq(THREAD_ID), eq(USER_ID), any());
    }

    @Test
    void shouldBackfillTaskAndWorkflowIdsAfterCommandCompletion() {
        WorkspaceThreadMessage streaming = new WorkspaceThreadMessage(
                "agent-1", THREAD_ID, USER_ID, "agent", "", "streaming",
                "cmd-1", null, null, null, Instant.parse("2026-08-21T10:00:00Z"));
        when(threadRepository.findMessageByIdAndUserId("agent-1", USER_ID))
                .thenReturn(Optional.of(streaming));
        when(taskRepository.findBySourceExecutionId(USER_ID, "cmd-1"))
                .thenReturn(List.of());
        when(workflowRepository.findBySourceExecutionIdAndUserId("cmd-1", USER_ID))
                .thenReturn(Optional.of(new DelegatedWorkflow(
                        "wf-1", USER_ID, "cmd-1", "原命令", "标题", "PLAN_EXECUTE", "RUNNING",
                        "{}", "{}", "", "", null, null, null)));
        when(commandService.execute(eq(USER_ID), any(), eq("cmd-1")))
                .thenReturn(new WorkspaceCommandResponse(
                        "cmd-1", "success", "delegated_task", "已创建 2 步骤工作流",
                        "委托任务已创建", false, List.of(), null, ""));

        service.runMessageCommand(USER_ID, THREAD_ID, "user-1", "agent-1", "cmd-1", "帮我和 km 约时间");

        org.mockito.ArgumentCaptor<WorkspaceThreadMessage> captor =
                org.mockito.ArgumentCaptor.forClass(WorkspaceThreadMessage.class);
        verify(threadRepository).updateMessage(captor.capture());
        WorkspaceThreadMessage updated = captor.getValue();
        assertThat(updated.status()).isEqualTo("done");
        assertThat(updated.content()).contains("委托任务已创建");
        assertThat(updated.workflowId()).isEqualTo("wf-1");
        assertThat(updated.resultJson()).contains("cmd-1");
    }

    @Test
    void shouldRecordErrorMessageWhenCommandFails() {
        WorkspaceThreadMessage streaming = new WorkspaceThreadMessage(
                "agent-2", THREAD_ID, USER_ID, "agent", "", "streaming",
                "cmd-2", null, null, null, Instant.parse("2026-08-21T10:00:00Z"));
        when(threadRepository.findMessageByIdAndUserId("agent-2", USER_ID))
                .thenReturn(Optional.of(streaming));
        when(commandService.execute(eq(USER_ID), any(), eq("cmd-2")))
                .thenThrow(new IllegalStateException("runtime timeout"));

        service.runMessageCommand(USER_ID, THREAD_ID, "user-2", "agent-2", "cmd-2", "做点什么");

        org.mockito.ArgumentCaptor<WorkspaceThreadMessage> captor =
                org.mockito.ArgumentCaptor.forClass(WorkspaceThreadMessage.class);
        verify(threadRepository).updateMessage(captor.capture());
        assertThat(captor.getValue().status()).isEqualTo("error");
        assertThat(captor.getValue().content()).contains("runtime timeout");
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

    @Test
    void shouldMarkStaleStreamingMessagesAsErrorOnList() {
        Instant stale = Instant.parse("2026-08-20T10:00:00Z");
        WorkspaceThreadMessage staleMessage = new WorkspaceThreadMessage(
                "msg-stale", THREAD_ID, USER_ID, "agent", "", "streaming",
                "cmd-stale", null, null, null, stale);
        when(threadRepository.findThreadByIdAndUserId(THREAD_ID, USER_ID))
                .thenReturn(Optional.of(thread()));
        when(threadRepository.listMessages(THREAD_ID, 50, null))
                .thenReturn(List.of(staleMessage));

        List<WorkspaceThreadMessageResponse> messages = service.listMessages(USER_ID, THREAD_ID, 50, null);

        assertThat(messages).hasSize(1);
        verify(threadRepository).updateMessageStatus(eq("msg-stale"), eq(USER_ID), eq("error"), anyString());
    }
}