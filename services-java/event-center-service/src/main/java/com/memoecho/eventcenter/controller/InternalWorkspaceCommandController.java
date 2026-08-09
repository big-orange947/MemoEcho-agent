package com.memoecho.eventcenter.controller;

import com.memoecho.eventcenter.dto.WorkspaceCommandRequest;
import com.memoecho.eventcenter.dto.WorkspaceCommandResponse;
import com.memoecho.eventcenter.dto.ConversationSummaryResponse;
import com.memoecho.eventcenter.dto.DelegatedTaskResponse;
import com.memoecho.eventcenter.dto.DelegatedTaskEventClaimRequest;
import com.memoecho.eventcenter.dto.DelegatedTaskEventClaimResponse;
import com.memoecho.eventcenter.dto.DelegatedTaskEventCompleteRequest;
import com.memoecho.eventcenter.dto.DelegatedTaskRuntimeCreateRequest;
import com.memoecho.eventcenter.dto.DelegatedTaskRuntimeUpdateRequest;
import com.memoecho.eventcenter.dto.DelegatedWorkflowCreateRequest;
import com.memoecho.eventcenter.dto.DelegatedWorkflowResponse;
import com.memoecho.eventcenter.dto.DelegatedWorkflowStepCompleteRequest;
import com.memoecho.eventcenter.service.DelegatedTaskApplicationService;
import com.memoecho.eventcenter.service.DelegatedWorkflowApplicationService;
import com.memoecho.eventcenter.service.LocalUserContextResolver;
import com.memoecho.eventcenter.service.WorkspaceCommandApplicationService;
import jakarta.validation.Valid;
import org.springframework.http.ResponseEntity;
import org.springframework.web.bind.annotation.PostMapping;
import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.PathVariable;
import org.springframework.web.bind.annotation.RequestParam;
import org.springframework.web.bind.annotation.RequestBody;
import org.springframework.web.bind.annotation.RequestHeader;
import org.springframework.web.bind.annotation.RequestMapping;
import org.springframework.web.bind.annotation.RestController;

import java.util.List;

@RestController
@RequestMapping("/internal/workspace/commands")
public class InternalWorkspaceCommandController {

    private final WorkspaceCommandApplicationService applicationService;
    private final DelegatedTaskApplicationService delegatedTaskApplicationService;
    private final DelegatedWorkflowApplicationService delegatedWorkflowApplicationService;
    private final LocalUserContextResolver userContextResolver;

    /**
     * 注入命令服务和用户解析器，确保桌面端不能伪造其他用户执行 Agent。
     */
    public InternalWorkspaceCommandController(
            WorkspaceCommandApplicationService applicationService,
            DelegatedTaskApplicationService delegatedTaskApplicationService,
            DelegatedWorkflowApplicationService delegatedWorkflowApplicationService,
            LocalUserContextResolver userContextResolver
    ) {
        this.applicationService = applicationService;
        this.delegatedTaskApplicationService = delegatedTaskApplicationService;
        this.delegatedWorkflowApplicationService = delegatedWorkflowApplicationService;
        this.userContextResolver = userContextResolver;
    }

    /**
     * 接收当前登录用户的桌面命令，并同步返回本次 Agent 编排结果。
     */
    @PostMapping
    public ResponseEntity<WorkspaceCommandResponse> execute(
            @RequestHeader(name = "Authorization", required = false) String authorization,
            @RequestHeader(name = "X-Memo-Echo-User-Id", defaultValue = "local-user") String userId,
            @Valid @RequestBody WorkspaceCommandRequest request
    ) {
        String resolvedUserId = userContextResolver.resolve(authorization, userId);
        return ResponseEntity.ok(applicationService.execute(resolvedUserId, request));
    }

    /** 查询当前用户最近创建的委托任务，供客户端恢复任务状态。 */
    @GetMapping("/delegated")
    public ResponseEntity<List<DelegatedTaskResponse>> listDelegatedTasks(
            @RequestHeader(name = "Authorization", required = false) String authorization,
            @RequestHeader(name = "X-Memo-Echo-User-Id", defaultValue = "local-user") String userId,
            @RequestParam(defaultValue = "20") int limit
    ) {
        String resolvedUserId = userContextResolver.resolve(authorization, userId);
        return ResponseEntity.ok(delegatedTaskApplicationService.list(resolvedUserId, limit));
    }

    /** Python Runtime 按会话恢复活动委托；服务令牌和用户归属必须同时通过校验。 */
    /** 读取当前用户拥有的单个委托任务详情。 */
    @GetMapping("/delegated/{taskId}")
    public ResponseEntity<DelegatedTaskResponse> getDelegatedTask(
            @RequestHeader(name = "Authorization", required = false) String authorization,
            @RequestHeader(name = "X-Memo-Echo-User-Id", defaultValue = "local-user") String userId,
            @PathVariable String taskId
    ) {
        String resolvedUserId = userContextResolver.resolve(authorization, userId);
        return ResponseEntity.ok(delegatedTaskApplicationService.get(resolvedUserId, taskId));
    }

    @GetMapping("/delegated/active")
    public ResponseEntity<DelegatedTaskResponse> findActiveDelegatedTask(
            @RequestHeader("X-Memo-Echo-Runtime-Token") String runtimeToken,
            @RequestHeader("X-Memo-Echo-User-Id") String userId,
            @RequestParam String platform,
            @RequestParam String chatType,
            @RequestParam String chatId
    ) {
        String resolvedUserId = userContextResolver.resolveRuntimeUser(runtimeToken, userId);
        return delegatedTaskApplicationService.findActive(resolvedUserId, platform, chatType, chatId)
                .map(ResponseEntity::ok)
                .orElseGet(() -> ResponseEntity.notFound().build());
    }

    /** Python Runtime 提交 LangGraph 进度或终态，客户端不能通过该接口伪造执行结果。 */
    @PostMapping("/delegated/{taskId}/runtime")
    public ResponseEntity<DelegatedTaskResponse> updateDelegatedTaskRuntime(
            @RequestHeader("X-Memo-Echo-Runtime-Token") String runtimeToken,
            @RequestHeader("X-Memo-Echo-User-Id") String userId,
            @PathVariable String taskId,
            @RequestBody DelegatedTaskRuntimeUpdateRequest request
    ) {
        String resolvedUserId = userContextResolver.resolveRuntimeUser(runtimeToken, userId);
        return ResponseEntity.ok(delegatedTaskApplicationService.updateRuntime(resolvedUserId, taskId, request));
    }

    /** Runtime 在执行事件前申请数据库级租约，防止重投与并发重复发送消息。 */
    @PostMapping("/delegated/{taskId}/events/claim")
    public ResponseEntity<DelegatedTaskEventClaimResponse> claimDelegatedTaskEvent(
            @RequestHeader("X-Memo-Echo-Runtime-Token") String runtimeToken,
            @RequestHeader("X-Memo-Echo-User-Id") String userId,
            @PathVariable String taskId,
            @RequestBody DelegatedTaskEventClaimRequest request
    ) {
        String resolvedUserId = userContextResolver.resolveRuntimeUser(runtimeToken, userId);
        return ResponseEntity.ok(delegatedTaskApplicationService.claimEvent(
                resolvedUserId, taskId, request.eventId(), request.leaseSeconds()));
    }

    /** Runtime 成功完成事件处理后关闭租约，确保后续重投不再重复执行。 */
    @PostMapping("/delegated/{taskId}/events/complete")
    public ResponseEntity<Void> completeDelegatedTaskEvent(
            @RequestHeader("X-Memo-Echo-Runtime-Token") String runtimeToken,
            @RequestHeader("X-Memo-Echo-User-Id") String userId,
            @PathVariable String taskId,
            @RequestBody DelegatedTaskEventCompleteRequest request
    ) {
        String resolvedUserId = userContextResolver.resolveRuntimeUser(runtimeToken, userId);
        delegatedTaskApplicationService.completeEvent(resolvedUserId, taskId, request.eventId(), request.claimToken());
        return ResponseEntity.noContent().build();
    }

    /** Runtime 编译主控台命令前读取联系人白名单，客户端不能绕过该接口直接给模型任意联系人。 */
    @GetMapping("/delegated/candidates")
    public ResponseEntity<List<ConversationSummaryResponse>> listDelegatedTaskCandidates(
            @RequestHeader("X-Memo-Echo-Runtime-Token") String runtimeToken,
            @RequestHeader("X-Memo-Echo-User-Id") String userId
    ) {
        String resolvedUserId = userContextResolver.resolveRuntimeUser(runtimeToken, userId);
        return ResponseEntity.ok(delegatedTaskApplicationService.listAuthorizedConversationCandidates(resolvedUserId));
    }

    /** Runtime 提交 LangGraph 编译后的任务，Java 负责白名单校验、状态初始化和持久化。 */
    @PostMapping("/delegated/runtime-create")
    public ResponseEntity<DelegatedTaskResponse> createDelegatedTaskFromRuntime(
            @RequestHeader("X-Memo-Echo-Runtime-Token") String runtimeToken,
            @RequestHeader("X-Memo-Echo-User-Id") String userId,
            @Valid @RequestBody DelegatedTaskRuntimeCreateRequest request
    ) {
        String resolvedUserId = userContextResolver.resolveRuntimeUser(runtimeToken, userId);
        return ResponseEntity.ok(delegatedTaskApplicationService.createCompiled(
                resolvedUserId, request.command(), request.executionId(), request.compilation()));
    }

    /**
     * Runtime 提交一条主控台命令生成的完整工作流。Java 会在一次事务内校验 DAG、联系人和事实依赖。
     */
    @PostMapping("/delegated-workflows/runtime")
    public ResponseEntity<DelegatedWorkflowResponse> createDelegatedWorkflowFromRuntime(
            @RequestHeader("X-Memo-Echo-Runtime-Token") String runtimeToken,
            @RequestHeader("X-Memo-Echo-User-Id") String userId,
            @Valid @RequestBody DelegatedWorkflowCreateRequest request
    ) {
        String resolvedUserId = userContextResolver.resolveRuntimeUser(runtimeToken, userId);
        return ResponseEntity.ok(delegatedWorkflowApplicationService.create(resolvedUserId, request));
    }

    /**
     * Runtime 读取工作流最新快照，用于执行步骤前核对状态和激活版本。
     */
    @GetMapping("/delegated-workflows/{workflowId}/runtime")
    public ResponseEntity<DelegatedWorkflowResponse> getDelegatedWorkflowForRuntime(
            @RequestHeader("X-Memo-Echo-Runtime-Token") String runtimeToken,
            @RequestHeader("X-Memo-Echo-User-Id") String userId,
            @PathVariable String workflowId
    ) {
        String resolvedUserId = userContextResolver.resolveRuntimeUser(runtimeToken, userId);
        return ResponseEntity.ok(delegatedWorkflowApplicationService.get(resolvedUserId, workflowId));
    }

    /**
     * Runtime 回报步骤完成结果。服务层会原子合并事实、解锁后继步骤并判断父工作流是否结束。
     */
    @PostMapping("/delegated-workflows/{workflowId}/steps/{stepKey}/complete")
    public ResponseEntity<DelegatedWorkflowResponse> completeDelegatedWorkflowStep(
            @RequestHeader("X-Memo-Echo-Runtime-Token") String runtimeToken,
            @RequestHeader("X-Memo-Echo-User-Id") String userId,
            @PathVariable String workflowId,
            @PathVariable String stepKey,
            @Valid @RequestBody DelegatedWorkflowStepCompleteRequest request
    ) {
        String resolvedUserId = userContextResolver.resolveRuntimeUser(runtimeToken, userId);
        return ResponseEntity.ok(delegatedWorkflowApplicationService.completeStep(
                resolvedUserId, workflowId, stepKey, request));
    }

    /**
     * 查询当前用户最近的父工作流，供客户端按“一条命令一张卡片”展示执行进度。
     */
    @GetMapping("/delegated-workflows")
    public ResponseEntity<List<DelegatedWorkflowResponse>> listDelegatedWorkflows(
            @RequestHeader(name = "Authorization", required = false) String authorization,
            @RequestHeader(name = "X-Memo-Echo-User-Id", defaultValue = "local-user") String userId,
            @RequestParam(defaultValue = "20") int limit
    ) {
        String resolvedUserId = userContextResolver.resolve(authorization, userId);
        return ResponseEntity.ok(delegatedWorkflowApplicationService.list(resolvedUserId, limit));
    }

    /**
     * 读取单个父工作流及其有序步骤，用户归属在服务层再次校验。
     */
    @GetMapping("/delegated-workflows/{workflowId}")
    public ResponseEntity<DelegatedWorkflowResponse> getDelegatedWorkflow(
            @RequestHeader(name = "Authorization", required = false) String authorization,
            @RequestHeader(name = "X-Memo-Echo-User-Id", defaultValue = "local-user") String userId,
            @PathVariable String workflowId
    ) {
        String resolvedUserId = userContextResolver.resolve(authorization, userId);
        return ResponseEntity.ok(delegatedWorkflowApplicationService.get(resolvedUserId, workflowId));
    }

    /** 用户确认任务后仅进入待执行队列，不在 HTTP 请求中直接发送外部消息。 */
    @PostMapping("/delegated/{taskId}/confirm")
    public ResponseEntity<DelegatedTaskResponse> confirmDelegatedTask(
            @RequestHeader(name = "Authorization", required = false) String authorization,
            @RequestHeader(name = "X-Memo-Echo-User-Id", defaultValue = "local-user") String userId,
            @PathVariable String taskId
    ) {
        String resolvedUserId = userContextResolver.resolve(authorization, userId);
        return ResponseEntity.ok(delegatedTaskApplicationService.confirm(resolvedUserId, taskId));
    }

    /** 取消尚未完成的委托任务。 */
    @PostMapping("/delegated/{taskId}/cancel")
    public ResponseEntity<DelegatedTaskResponse> cancelDelegatedTask(
            @RequestHeader(name = "Authorization", required = false) String authorization,
            @RequestHeader(name = "X-Memo-Echo-User-Id", defaultValue = "local-user") String userId,
            @PathVariable String taskId
    ) {
        String resolvedUserId = userContextResolver.resolve(authorization, userId);
        return ResponseEntity.ok(delegatedTaskApplicationService.cancel(resolvedUserId, taskId));
    }

    /** 暂停正在执行的委托，暂停期间 Runtime 不会再按会话恢复该任务。 */
    @PostMapping("/delegated/{taskId}/pause")
    public ResponseEntity<DelegatedTaskResponse> pauseDelegatedTask(
            @RequestHeader(name = "Authorization", required = false) String authorization,
            @RequestHeader(name = "X-Memo-Echo-User-Id", defaultValue = "local-user") String userId,
            @PathVariable String taskId
    ) {
        String resolvedUserId = userContextResolver.resolve(authorization, userId);
        return ResponseEntity.ok(delegatedTaskApplicationService.pause(resolvedUserId, taskId));
    }

    /** 继续已暂停的委托，并复用暂停前保存的 LangGraph 状态。 */
    @PostMapping("/delegated/{taskId}/resume")
    public ResponseEntity<DelegatedTaskResponse> resumeDelegatedTask(
            @RequestHeader(name = "Authorization", required = false) String authorization,
            @RequestHeader(name = "X-Memo-Echo-User-Id", defaultValue = "local-user") String userId,
            @PathVariable String taskId
    ) {
        String resolvedUserId = userContextResolver.resolve(authorization, userId);
        return ResponseEntity.ok(delegatedTaskApplicationService.resume(resolvedUserId, taskId));
    }

    /** 用户主动结束委托并阻止后续消息继续触发代理。 */
    @PostMapping("/delegated/{taskId}/complete")
    public ResponseEntity<DelegatedTaskResponse> completeDelegatedTask(
            @RequestHeader(name = "Authorization", required = false) String authorization,
            @RequestHeader(name = "X-Memo-Echo-User-Id", defaultValue = "local-user") String userId,
            @PathVariable String taskId
    ) {
        String resolvedUserId = userContextResolver.resolve(authorization, userId);
        return ResponseEntity.ok(delegatedTaskApplicationService.complete(resolvedUserId, taskId));
    }
}
