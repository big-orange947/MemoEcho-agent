package com.memoecho.eventcenter.service;

import com.memoecho.eventcenter.config.DelegatedWorkflowDispatchProperties;
import com.memoecho.eventcenter.model.DelegatedWorkflowStepDispatch;
import com.memoecho.eventcenter.repository.JdbcDelegatedWorkflowStepDispatchRepository;
import org.springframework.stereotype.Service;
import org.springframework.transaction.annotation.Transactional;

import java.time.Duration;
import java.time.Instant;
import java.util.List;
import java.util.Optional;

/**
 * 管理步骤投递的短事务租约。
 * 网络调用不能放进数据库事务，因此认领、成功确认和失败退避分别使用独立短事务。
 */
@Service
public class DelegatedWorkflowStepDispatchLeaseService {

    private static final int MAX_ERROR_LENGTH = 2000;

    private final JdbcDelegatedWorkflowStepDispatchRepository repository;
    private final DelegatedWorkflowDispatchProperties properties;

    public DelegatedWorkflowStepDispatchLeaseService(
            JdbcDelegatedWorkflowStepDispatchRepository repository,
            DelegatedWorkflowDispatchProperties properties
    ) {
        this.repository = repository;
        this.properties = properties;
    }

    /** 查询当前到期的候选投递；这里只读候选 ID，不代表当前实例已经取得执行权。 */
    @Transactional(readOnly = true)
    public List<Long> findDueIds() {
        return repository.findDueIds(Instant.now(), Math.max(1, properties.getBatchSize()));
    }

    /**
     * 对账运行中的工作流，为缺少 outbox 记录的 ACTIVE 步骤补写待投递记录。
     *
     * <p>该操作只执行数据库写入，不包含网络调用，适合由调度器周期性执行。</p>
     */
    @Transactional
    public int reconcileMissingActiveStepDispatches() {
        return repository.enqueueMissingActiveSteps(Instant.now());
    }

    /**
     * 原子认领一个候选并返回带最新 attemptCount 的投递。
     * 条件更新失败说明其他实例已经抢占，此时返回空值并跳过。
     */
    @Transactional
    public Optional<DelegatedWorkflowStepDispatch> claim(long id) {
        Instant now = Instant.now();
        Instant leaseUntil = now.plusSeconds(Math.max(1, properties.getLeaseSeconds()));
        if (repository.claim(id, now, leaseUntil) != 1) {
            return Optional.empty();
        }
        return repository.findById(id)
                .filter(dispatch -> "PROCESSING".equalsIgnoreCase(dispatch.status()));
    }

    /** 使用投递的 attemptCount 作为租约栅栏，只有当前租约持有者可以确认成功。 */
    @Transactional
    public void markSucceeded(DelegatedWorkflowStepDispatch dispatch) {
        repository.markSucceeded(dispatch.id(), dispatch.attemptCount(), Instant.now());
    }

    /**
     * 按尝试次数计算指数退避并释放租约。
     * 错误文本会截断，防止第三方响应把数据库行无限放大。
     */
    @Transactional
    public void scheduleRetry(DelegatedWorkflowStepDispatch dispatch, String error) {
        Instant now = Instant.now();
        long delaySeconds = retryDelaySeconds(dispatch.attemptCount());
        repository.scheduleRetry(
                dispatch.id(), dispatch.attemptCount(), now.plusSeconds(delaySeconds),
                truncate(error), now);
    }

    /** 计算封顶的指数退避时间，避免位移溢出和故障期间高频重试。 */
    long retryDelaySeconds(int attemptCount) {
        long initial = Math.max(1, properties.getInitialRetrySeconds());
        long maximum = Math.max(initial, properties.getMaxRetrySeconds());
        int exponent = Math.max(0, Math.min(attemptCount - 1, 30));
        long multiplier = 1L << exponent;
        if (initial > maximum / multiplier) {
            return maximum;
        }
        return Math.min(maximum, initial * multiplier);
    }

    /** 将可能为空的异常信息规范化为适合数据库存储的短文本。 */
    private String truncate(String error) {
        String value = error == null || error.isBlank() ? "未知 Runtime 调用错误" : error.trim();
        return value.length() <= MAX_ERROR_LENGTH ? value : value.substring(0, MAX_ERROR_LENGTH);
    }
}
