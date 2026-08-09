package com.memoecho.eventcenter.service;

import com.memoecho.eventcenter.config.DelegatedWorkflowDispatchProperties;
import com.memoecho.eventcenter.model.DelegatedWorkflowStepDispatch;
import com.memoecho.eventcenter.repository.JdbcDelegatedWorkflowStepDispatchRepository;
import org.junit.jupiter.api.Test;

import java.time.Instant;
import java.util.Optional;

import static org.assertj.core.api.Assertions.assertThat;
import static org.mockito.ArgumentMatchers.any;
import static org.mockito.BDDMockito.given;
import static org.mockito.Mockito.mock;
import static org.mockito.Mockito.never;
import static org.mockito.Mockito.verify;

class DelegatedWorkflowStepDispatchLeaseServiceTest {

    private final JdbcDelegatedWorkflowStepDispatchRepository repository =
            mock(JdbcDelegatedWorkflowStepDispatchRepository.class);
    private final DelegatedWorkflowDispatchProperties properties = properties();
    private final DelegatedWorkflowStepDispatchLeaseService service =
            new DelegatedWorkflowStepDispatchLeaseService(repository, properties);

    @Test
    void shouldReconcileMissingActiveStepDispatches() {
        // 验证租约服务会把对账操作放在独立短事务中，并返回实际恢复数量。
        given(repository.enqueueMissingActiveSteps(any(Instant.class))).willReturn(2);

        int repaired = service.reconcileMissingActiveStepDispatches();

        assertThat(repaired).isEqualTo(2);
        verify(repository).enqueueMissingActiveSteps(any(Instant.class));
    }

    @Test
    void shouldNotLoadDispatchWhenAnotherInstanceWonTheLease() {
        // 这个测试验证条件更新失败时立即放弃，避免两个服务实例重复执行同一个步骤。
        given(repository.claim(any(Long.class), any(Instant.class), any(Instant.class))).willReturn(0);

        Optional<DelegatedWorkflowStepDispatch> result = service.claim(17L);

        assertThat(result).isEmpty();
        verify(repository, never()).findById(17L);
    }

    @Test
    void shouldReturnOnlyTheProcessingDispatchAfterClaimingIt() {
        // 这个测试验证认领成功后返回数据库中的最新记录，后续确认会使用最新 attemptCount 作为租约栅栏。
        DelegatedWorkflowStepDispatch dispatch = dispatch("PROCESSING", 3);
        given(repository.claim(any(Long.class), any(Instant.class), any(Instant.class))).willReturn(1);
        given(repository.findById(17L)).willReturn(Optional.of(dispatch));

        Optional<DelegatedWorkflowStepDispatch> result = service.claim(17L);

        assertThat(result).contains(dispatch);
    }

    @Test
    void shouldRejectARecordThatIsNoLongerProcessing() {
        // 这个测试验证认领后的二次读取仍会检查状态，防止异常数据绕过租约状态机。
        given(repository.claim(any(Long.class), any(Instant.class), any(Instant.class))).willReturn(1);
        given(repository.findById(17L)).willReturn(Optional.of(dispatch("SUCCEEDED", 3)));

        Optional<DelegatedWorkflowStepDispatch> result = service.claim(17L);

        assertThat(result).isEmpty();
    }

    @Test
    void shouldUseCappedExponentialRetryDelay() {
        // 这个测试验证失败重试按尝试次数指数增长，并在配置的最大值处封顶。
        assertThat(service.retryDelaySeconds(1)).isEqualTo(2L);
        assertThat(service.retryDelaySeconds(2)).isEqualTo(4L);
        assertThat(service.retryDelaySeconds(3)).isEqualTo(8L);
        assertThat(service.retryDelaySeconds(6)).isEqualTo(60L);
        assertThat(service.retryDelaySeconds(31)).isEqualTo(60L);
    }

    /** 创建测试使用的退避和租约配置，避免依赖 Spring 上下文。 */
    private DelegatedWorkflowDispatchProperties properties() {
        DelegatedWorkflowDispatchProperties value = new DelegatedWorkflowDispatchProperties();
        value.setLeaseSeconds(30);
        value.setInitialRetrySeconds(2);
        value.setMaxRetrySeconds(60);
        return value;
    }

    /** 创建指定状态和尝试次数的步骤投递记录。 */
    private DelegatedWorkflowStepDispatch dispatch(String status, int attemptCount) {
        return new DelegatedWorkflowStepDispatch(
                17L,
                "workflow-1",
                "ask-contact",
                2L,
                "task-1",
                "user-1",
                status,
                attemptCount,
                Instant.parse("2026-08-09T08:00:00Z"),
                Instant.parse("2026-08-09T08:01:00Z"),
                null
        );
    }
}
