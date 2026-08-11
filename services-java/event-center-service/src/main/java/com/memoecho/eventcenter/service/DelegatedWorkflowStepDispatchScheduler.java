package com.memoecho.eventcenter.service;

import com.memoecho.eventcenter.config.DelegatedWorkflowDispatchProperties;
import com.memoecho.eventcenter.dto.DelegatedWorkflowStepExecutionRequest;
import com.memoecho.eventcenter.dto.DispatchResult;
import com.memoecho.eventcenter.model.DelegatedWorkflowStepDispatch;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.scheduling.annotation.Scheduled;
import org.springframework.stereotype.Component;

/** 持续把已提交的工作流步骤可靠投递给 Python Runtime。 */
@Component
public class DelegatedWorkflowStepDispatchScheduler {

    private static final Logger log = LoggerFactory.getLogger(DelegatedWorkflowStepDispatchScheduler.class);

    private final DelegatedWorkflowDispatchProperties properties;
    private final DelegatedWorkflowStepDispatchLeaseService leaseService;
    private final AgentRuntimeDispatchClient runtimeClient;

    public DelegatedWorkflowStepDispatchScheduler(
            DelegatedWorkflowDispatchProperties properties,
            DelegatedWorkflowStepDispatchLeaseService leaseService,
            AgentRuntimeDispatchClient runtimeClient
    ) {
        this.properties = properties;
        this.leaseService = leaseService;
        this.runtimeClient = runtimeClient;
    }

    /**
     * 周期扫描到期投递并逐个执行。
     * 每条网络请求都位于租约事务之外，单条失败不会阻塞同一批次中的其他步骤。
     */
    @Scheduled(fixedDelayString = "${event-center.delegated-workflow-dispatch.poll-interval-ms:1000}")
    public void dispatchDueSteps() {
        if (!properties.isEnabled()) {
            return;
        }
        reconcileMissingDispatches();
        for (Long id : leaseService.findDueIds()) {
            try {
                leaseService.claim(id).ifPresent(this::dispatchClaimedStep);
            } catch (RuntimeException exception) {
                log.error("认领委托工作流步骤失败。dispatchId={}", id, exception);
            }
        }
    }

    /**
     * 在扫描 outbox 前修复遗漏记录，避免 ACTIVE 步骤因为没有投递行而永久停滞。
     * 对账采用数据库唯一键保证幂等，多实例同时执行也不会生成重复投递。
     */
    private void reconcileMissingDispatches() {
        try {
            int repaired = leaseService.reconcileMissingActiveStepDispatches();
            if (repaired > 0) {
                log.info("已恢复遗漏的委托工作流步骤投递。count={}", repaired);
            }
        } catch (RuntimeException exception) {
            // 对账失败不应阻止已有 outbox 继续投递；下一轮调度会再次尝试修复。
            log.error("委托工作流步骤投递对账失败，本轮继续处理已有投递。", exception);
        }
    }

    /** 调用 Runtime，并依据明确的业务状态确认成功，否则安排下一次重试。 */
    private void dispatchClaimedStep(DelegatedWorkflowStepDispatch dispatch) {
        DelegatedWorkflowStepExecutionRequest request = new DelegatedWorkflowStepExecutionRequest(
                dispatch.workflowId(), dispatch.stepKey(), dispatch.activationVersion(),
                dispatch.taskId(), dispatch.userId(), dispatch.idempotencyKey());
        DispatchResult result;
        try {
            result = runtimeClient.executeDelegatedWorkflowStep(request);
        } catch (RuntimeException exception) {
            leaseService.scheduleRetry(dispatch, exception.getMessage());
            log.warn("委托步骤调用 Runtime 异常，已安排重试。key={}", dispatch.idempotencyKey(), exception);
            return;
        }

        if (isSuccessful(result)) {
            leaseService.markSucceeded(dispatch);
            log.info("委托工作流步骤投递成功。key={}, attempt={}",
                    dispatch.idempotencyKey(), dispatch.attemptCount());
            return;
        }
        String error = describeFailure(result);
        leaseService.scheduleRetry(dispatch, error);
        log.warn("委托工作流步骤投递失败，已安排重试。key={}, attempt={}, error={}",
                dispatch.idempotencyKey(), dispatch.attemptCount(), error);
    }

    /** 只有 Runtime 返回 2xx 且明确声明 executed/ignored，outbox 才能确认完成。 */
    private boolean isSuccessful(DispatchResult result) {
        if (result == null || !result.attempted() || result.httpStatus() == null
                || result.httpStatus() < 200 || result.httpStatus() >= 300 || result.body() == null) {
            return false;
        }
        String runtimeStatus = result.body().path("status").asText("");
        return "executed".equalsIgnoreCase(runtimeStatus) || "ignored".equalsIgnoreCase(runtimeStatus);
    }

    /** 汇总 HTTP 状态与错误内容，给运维日志和下一次重试保留足够上下文。 */
    private String describeFailure(DispatchResult result) {
        if (result == null) {
            return "Runtime 未返回调用结果";
        }
        String status = result.httpStatus() == null ? "无 HTTP 状态" : "HTTP " + result.httpStatus();
        if (result.body() != null && result.httpStatus() != null
                && result.httpStatus() >= 200 && result.httpStatus() < 300) {
            String runtimeStatus = result.body().path("status").asText("missing");
            String reason = result.body().path("reason").asText("");
            return status + ": Runtime status=" + runtimeStatus
                    + (reason.isBlank() ? "" : ", reason=" + reason);
        }
        String error = result.error() == null || result.error().isBlank() ? "未提供错误详情" : result.error();
        return status + ": " + error;
    }
}
