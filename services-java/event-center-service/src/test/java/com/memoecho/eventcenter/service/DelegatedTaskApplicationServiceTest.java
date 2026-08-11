package com.memoecho.eventcenter.service;

import com.memoecho.eventcenter.dto.ConversationSummaryResponse;
import com.memoecho.eventcenter.dto.DelegatedTaskCompilationResponse;
import com.memoecho.eventcenter.dto.QqContactResponse;
import com.memoecho.eventcenter.model.DelegatedTask;
import com.memoecho.eventcenter.repository.JdbcDelegatedTaskRepository;
import com.memoecho.eventcenter.repository.JdbcDelegatedTaskEventClaimRepository;
import org.junit.jupiter.api.Test;
import org.springframework.dao.DuplicateKeyException;

import java.time.Instant;
import java.util.List;
import java.util.Optional;

import static org.assertj.core.api.Assertions.assertThat;
import static org.mockito.ArgumentMatchers.any;
import static org.mockito.ArgumentMatchers.eq;
import static org.mockito.Mockito.mock;
import static org.mockito.Mockito.verify;
import static org.mockito.Mockito.when;

/** 验证委托应用服务使用用户隔离会话，并保持确认式状态流转。 */
class DelegatedTaskApplicationServiceTest {

    private final DelegatedTaskIntentParser parser = mock(DelegatedTaskIntentParser.class);
    private final JdbcDelegatedTaskRepository repository = mock(JdbcDelegatedTaskRepository.class);
    private final JdbcDelegatedTaskEventClaimRepository eventClaimRepository = mock(JdbcDelegatedTaskEventClaimRepository.class);
    private final EventCenterApplicationService eventCenter = mock(EventCenterApplicationService.class);
    private final QqConnectorContactClient contactClient = mock(QqConnectorContactClient.class);
    private final AgentRuntimeDispatchClient runtimeDispatchClient = mock(AgentRuntimeDispatchClient.class);
    private final DelegatedTaskApplicationService service = new DelegatedTaskApplicationService(
            parser, repository, eventClaimRepository, eventCenter, contactClient, runtimeDispatchClient);

    /** 创建任务时只能读取当前用户的会话摘要，不能使用全局会话列表。 */
    @Test
    void shouldResolveTargetFromOwnedConversationsOnly() {
        List<ConversationSummaryResponse> owned = List.of();
        DelegatedTask task = task("task-1", "WAITING_TARGET", "");
        when(eventCenter.findConversationSummariesForUser("freeze", null, null, null, null, null))
                .thenReturn(owned);
        when(parser.parse("freeze", "帮我回一下小号的消息", owned)).thenReturn(Optional.of(task));
        // Runtime 未返回编译结果时，服务会把本地解析草稿升级为可运行任务后再持久化。
        when(repository.insert(any(DelegatedTask.class))).thenAnswer(invocation -> invocation.getArgument(0));

        var response = service.tryCreate("freeze", "帮我回一下小号的消息", "").orElseThrow();

        assertThat(response.id()).isEqualTo("task-1");
        verify(eventCenter).findConversationSummariesForUser("freeze", null, null, null, null, null);
    }

    /** 即使联系人尚无本地聊天摘要，也必须把 NapCat 实时好友加入委托目标白名单。 */
    @Test
    void shouldIncludeLiveQqContactWithoutConversationHistory() {
        String command = "帮我跟km预约一下明天家教的时间";
        DelegatedTask task = task("task-live", "WAITING_CONFIRMATION", "3807050597");
        when(eventCenter.findConversationSummariesForUser("freeze", null, null, null, null, null))
                .thenReturn(List.of());
        when(contactClient.listContacts("freeze"))
                .thenReturn(List.of(new QqContactResponse(
                        "3807050597", "km", "private", "km", List.of("km", "刘畅", "3807050597"))));
        when(parser.parse(eq("freeze"), eq(command), any())).thenAnswer(invocation -> {
            List<ConversationSummaryResponse> candidates = invocation.getArgument(2);
            assertThat(candidates).singleElement().satisfies(candidate -> {
                assertThat(candidate.chatId()).isEqualTo("3807050597");
                assertThat(candidate.chatName()).isEqualTo("km");
                assertThat(candidate.chatType()).isEqualTo("private");
                assertThat(candidate.aliases()).containsExactly("km", "刘畅", "3807050597");
            });
            return Optional.of(task);
        });
        when(repository.insert(any(DelegatedTask.class))).thenAnswer(invocation -> invocation.getArgument(0));

        var response = service.tryCreate("freeze", command, "").orElseThrow();

        assertThat(response.chatId()).isEqualTo("3807050597");
    }

    /** 两个并发请求越过前置查询后撞唯一键时，应返回另一请求已经创建的任务。 */
    @Test
    void shouldReuseWinningTaskWhenExecutionInsertHitsUniqueKey() {
        String command = "通知 km 明晚七点上课";
        String executionId = "desktop-race-001";
        DelegatedTask winner = task("task-winner", "ACTIVE", "3807050597");
        DelegatedTaskCompilationResponse compilation = new DelegatedTaskCompilationResponse(
                true,
                "CONVERSATION_GOAL",
                "km",
                "qq",
                "private",
                "3807050597",
                "km",
                "通知 km 明晚七点上课",
                "对方确认收到通知",
                "明晚七点",
                0.95d,
                "",
                false,
                "AUTO_COMPLETE",
                "准备通知对方",
                "{}"
        );
        when(eventCenter.findConversationSummariesForUser("freeze", null, null, null, null, null))
                .thenReturn(List.of());
        when(contactClient.listContacts("freeze"))
                .thenReturn(List.of(new QqContactResponse(
                        "3807050597", "km", "private", "km", List.of("km", "3807050597"))));
        // 第一次查询模拟两个请求同时未发现记录；唯一键冲突后的第二次查询返回赢家。
        when(repository.findBySourceExecutionAndTarget(
                "freeze", executionId, "qq", "private", "3807050597"
        )).thenReturn(Optional.empty(), Optional.of(winner));
        when(repository.findRecentDuplicateCommand(
                eq("freeze"), eq(command), eq("qq"), eq("private"), eq("3807050597"), any(Instant.class)
        )).thenReturn(Optional.empty());
        when(repository.insert(any(DelegatedTask.class)))
                .thenThrow(new DuplicateKeyException("simulated race"));

        var response = service.createCompiled("freeze", command, executionId, compilation);

        assertThat(response.id()).isEqualTo("task-winner");
        verify(repository).insert(any(DelegatedTask.class));
    }

    /** 修复上线后，刷新列表应自动恢复此前因兼容字符未命中的待选联系人任务。 */
    @Test
    void shouldRepairWaitingTargetWhenContactNowMatchesUniquely() {
        DelegatedTask waiting = task("task-repair", "WAITING_TARGET", "");
        DelegatedTask resolved = task("task-repair", "WAITING_CONFIRMATION", "3807050597");
        DelegatedTask active = task("task-repair", "ACTIVE", "3807050597");
        when(repository.findRecentByUserId("freeze", 20)).thenReturn(List.of(waiting));
        when(eventCenter.findConversationSummariesForUser("freeze", null, null, null, null, null))
                .thenReturn(List.of());
        when(contactClient.listContacts("freeze"))
                .thenReturn(List.of(new QqContactResponse("3807050597", "㎞", "private", "")));
        when(parser.parse(eq("freeze"), eq(waiting.originalCommand()), any())).thenReturn(Optional.of(resolved));
        when(repository.bindWaitingTarget("task-repair", "freeze", resolved)).thenReturn(Optional.of(active));

        var responses = service.list("freeze", 20);

        assertThat(responses).singleElement().satisfies(response -> {
            assertThat(response.status()).isEqualTo("ACTIVE");
            assertThat(response.chatId()).isEqualTo("3807050597");
        });
        verify(repository).bindWaitingTarget("task-repair", "freeze", resolved);
    }

    /** 已绑定目标的任务经用户确认后才进入 READY。 */
    @Test
    void shouldMoveConfirmedTaskToReady() {
        DelegatedTask waiting = task("task-2", "WAITING_CONFIRMATION", "3807050597");
        DelegatedTask active = task("task-2", "ACTIVE", "3807050597");
        when(repository.findByIdAndUserId("task-2", "freeze")).thenReturn(Optional.of(waiting));
        when(repository.updateRuntimeState(
                "task-2", "freeze", "ACTIVE", "任务已启动", waiting.stateJson(), "", ""
        )).thenReturn(Optional.of(active));

        var response = service.confirm("freeze", "task-2");

        assertThat(response.status()).isEqualTo("ACTIVE");
        assertThat(response.requiresConfirmation()).isFalse();
    }

    /** 暂停任务时必须保留 LangGraph 状态，避免继续执行后丢失历史进度。 */
    @Test
    void shouldPauseActiveTaskWithoutLosingRuntimeState() {
        DelegatedTask active = task("task-3", "ACTIVE", "3807050597");
        DelegatedTask paused = task("task-3", "PAUSED", "3807050597");
        when(repository.findByIdAndUserId("task-3", "freeze")).thenReturn(Optional.of(active));
        when(repository.updateRuntimeState(
                eq("task-3"), eq("freeze"), eq("PAUSED"), eq("任务已由用户暂停"),
                eq(active.stateJson()), eq(active.lastEventId()), eq(active.completionReport())
        )).thenReturn(Optional.of(paused));

        var response = service.pause("freeze", "task-3");

        assertThat(response.status()).isEqualTo("PAUSED");
    }

    /** 继续任务时从持久化图状态恢复为 ACTIVE，而不是创建一条新任务。 */
    @Test
    void shouldResumePausedTask() {
        DelegatedTask paused = task("task-4", "PAUSED", "3807050597");
        DelegatedTask active = task("task-4", "ACTIVE", "3807050597");
        when(repository.findByIdAndUserId("task-4", "freeze")).thenReturn(Optional.of(paused));
        when(repository.updateRuntimeState(
                eq("task-4"), eq("freeze"), eq("ACTIVE"), eq("任务已继续执行"),
                eq(paused.stateJson()), eq(paused.lastEventId()), eq(paused.completionReport())
        )).thenReturn(Optional.of(active));

        var response = service.resume("freeze", "task-4");

        assertThat(response.status()).isEqualTo("ACTIVE");
    }

    /** 用户手动结束后任务进入终态，并生成可展示的结束报告。 */
    @Test
    void shouldCompleteRunningTask() {
        DelegatedTask active = task("task-5", "ACTIVE", "3807050597");
        DelegatedTask completed = task("task-5", "COMPLETED", "3807050597");
        when(repository.findByIdAndUserId("task-5", "freeze")).thenReturn(Optional.of(active));
        when(repository.updateRuntimeState(
                eq("task-5"), eq("freeze"), eq("COMPLETED"), eq("任务已由用户手动结束"),
                eq(active.stateJson()), eq(active.lastEventId()), eq("用户在控制台手动结束了该委托任务。")
        )).thenReturn(Optional.of(completed));

        var response = service.complete("freeze", "task-5");

        assertThat(response.status()).isEqualTo("COMPLETED");
    }

    /** 构造具有确定时间和目标的委托任务。 */
    /**
     * 旧版本若提前把事件认领标记为完成，服务层应先验证任务归属，
     * 再把规范化后的事件标识交给仓储层按持久化结果决定是否恢复。
     */
    @Test
    void shouldRecoverDormantCompletedEventForOwnedTask() {
        DelegatedTask active = task("task-recover", "ACTIVE", "3807050597");
        when(repository.findByIdAndUserId("task-recover", "freeze")).thenReturn(Optional.of(active));
        when(eventClaimRepository.recoverDormantCompleted(
                "task-recover", "freeze", "event-001"
        )).thenReturn(true);

        boolean recovered = service.recoverDormantCompletedEvent(
                "freeze", "task-recover", "  event-001  "
        );

        assertThat(recovered).isTrue();
        verify(eventClaimRepository).recoverDormantCompleted(
                "task-recover", "freeze", "event-001"
        );
    }

    /** 构造具有固定用户、时间与目标会话的测试任务。 */
    private DelegatedTask task(String id, String status, String chatId) {
        Instant now = Instant.parse("2026-07-20T08:00:00Z");
        return new DelegatedTask(
                id, "freeze", "REPLY_ONCE", status, "帮我回一下小号的消息", "小号",
                "qq", "private", chatId, chatId.isBlank() ? "" : "小号", "回复小号",
                "生成回复草稿并确认", "", 0.88d, "请确认",
                !"READY".equals(status) && !"ACTIVE".equals(status), now, now
        );
    }
}
