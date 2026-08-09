package com.memoecho.eventcenter.service;

import com.memoecho.eventcenter.config.DelegatedWorkflowDispatchProperties;
import com.memoecho.eventcenter.dto.DelegatedWorkflowStepExecutionRequest;
import com.memoecho.eventcenter.dto.DispatchResult;
import com.memoecho.eventcenter.model.DelegatedWorkflowStepDispatch;
import org.junit.jupiter.api.Test;

import java.time.Instant;
import java.util.List;
import java.util.Optional;

import static org.mockito.ArgumentMatchers.any;
import static org.mockito.BDDMockito.given;
import static org.mockito.Mockito.mock;
import static org.mockito.Mockito.never;
import static org.mockito.Mockito.verify;

class DelegatedWorkflowStepDispatchSchedulerTest {

    private final DelegatedWorkflowStepDispatchLeaseService leaseService =
            mock(DelegatedWorkflowStepDispatchLeaseService.class);
    private final AgentRuntimeDispatchClient runtimeClient = mock(AgentRuntimeDispatchClient.class);

    @Test
    void shouldMarkClaimedStepSucceededAfterRuntimeAcceptedIt() {
        // 这个测试验证 Runtime 明确返回 2xx 后，outbox 才会被确认完成。
        DelegatedWorkflowStepDispatch dispatch = dispatch();
        DelegatedWorkflowStepDispatchScheduler scheduler = scheduler(true);
        given(leaseService.findDueIds()).willReturn(List.of(dispatch.id()));
        given(leaseService.claim(dispatch.id())).willReturn(Optional.of(dispatch));
        given(runtimeClient.executeDelegatedWorkflowStep(any(DelegatedWorkflowStepExecutionRequest.class)))
                .willReturn(new DispatchResult(true, 200, null, null));

        scheduler.dispatchDueSteps();

        verify(runtimeClient).executeDelegatedWorkflowStep(new DelegatedWorkflowStepExecutionRequest(
                dispatch.workflowId(), dispatch.stepKey(), dispatch.activationVersion(),
                dispatch.taskId(), dispatch.userId(), dispatch.idempotencyKey()));
        verify(leaseService).markSucceeded(dispatch);
        verify(leaseService, never()).scheduleRetry(any(), any());
    }

    @Test
    void shouldReconcileMissingDispatchesBeforePolling() {
        // 验证调度器每轮都会先修复遗漏 outbox，再扫描本轮到期记录。
        DelegatedWorkflowStepDispatchScheduler scheduler = scheduler(true);
        given(leaseService.reconcileMissingActiveStepDispatches()).willReturn(1);
        given(leaseService.findDueIds()).willReturn(List.of());

        scheduler.dispatchDueSteps();

        verify(leaseService).reconcileMissingActiveStepDispatches();
        verify(leaseService).findDueIds();
    }

    @Test
    void shouldRetryWhenRuntimeReturnsNonSuccessfulResult() {
        // 这个测试验证未尝试、非 2xx 等模糊结果不会被误认为已经投递成功。
        DelegatedWorkflowStepDispatch dispatch = dispatch();
        DelegatedWorkflowStepDispatchScheduler scheduler = scheduler(true);
        given(leaseService.findDueIds()).willReturn(List.of(dispatch.id()));
        given(leaseService.claim(dispatch.id())).willReturn(Optional.of(dispatch));
        given(runtimeClient.executeDelegatedWorkflowStep(any(DelegatedWorkflowStepExecutionRequest.class)))
                .willReturn(new DispatchResult(true, 503, null, "Runtime unavailable"));

        scheduler.dispatchDueSteps();

        verify(leaseService).scheduleRetry(dispatch, "HTTP 503: Runtime unavailable");
        verify(leaseService, never()).markSucceeded(any());
    }

    @Test
    void shouldRetryWhenRuntimeInvocationThrows() {
        // 这个测试验证网络异常只影响当前步骤，并由 outbox 在之后继续重试。
        DelegatedWorkflowStepDispatch dispatch = dispatch();
        DelegatedWorkflowStepDispatchScheduler scheduler = scheduler(true);
        given(leaseService.findDueIds()).willReturn(List.of(dispatch.id()));
        given(leaseService.claim(dispatch.id())).willReturn(Optional.of(dispatch));
        given(runtimeClient.executeDelegatedWorkflowStep(any(DelegatedWorkflowStepExecutionRequest.class)))
                .willThrow(new IllegalStateException("connection reset"));

        scheduler.dispatchDueSteps();

        verify(leaseService).scheduleRetry(dispatch, "connection reset");
        verify(leaseService, never()).markSucceeded(any());
    }

    @Test
    void shouldNotPollWhenDispatchIsDisabled() {
        // 这个测试验证关闭开关后不读取 outbox，也不会意外调用 Python Runtime。
        scheduler(false).dispatchDueSteps();

        verify(leaseService, never()).reconcileMissingActiveStepDispatches();
        verify(leaseService, never()).findDueIds();
        verify(runtimeClient, never()).executeDelegatedWorkflowStep(any());
    }

    /** 创建使用指定启用状态的调度器，保持每个测试只关注一个投递结果。 */
    private DelegatedWorkflowStepDispatchScheduler scheduler(boolean enabled) {
        DelegatedWorkflowDispatchProperties properties = new DelegatedWorkflowDispatchProperties();
        properties.setEnabled(enabled);
        return new DelegatedWorkflowStepDispatchScheduler(properties, leaseService, runtimeClient);
    }

    /** 创建一条已经被租约服务认领的工作流步骤投递。 */
    private DelegatedWorkflowStepDispatch dispatch() {
        return new DelegatedWorkflowStepDispatch(
                17L,
                "workflow-1",
                "ask-contact",
                2L,
                "task-1",
                "user-1",
                "PROCESSING",
                3,
                Instant.parse("2026-08-09T08:00:00Z"),
                Instant.parse("2026-08-09T08:01:00Z"),
                null
        );
    }
}
