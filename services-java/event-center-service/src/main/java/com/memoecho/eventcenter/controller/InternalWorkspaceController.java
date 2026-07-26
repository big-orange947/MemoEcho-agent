package com.memoecho.eventcenter.controller;

import com.fasterxml.jackson.databind.JsonNode;
import com.memoecho.eventcenter.dto.GroupOperationConfirmationRequest;
import com.memoecho.eventcenter.dto.WorkspaceBriefingResponse;
import com.memoecho.eventcenter.dto.WorkspaceInboxResponse;
import com.memoecho.eventcenter.service.WorkspaceBriefingApplicationService;
import com.memoecho.eventcenter.service.WorkspaceInboxApplicationService;
import com.memoecho.eventcenter.service.LocalUserContextResolver;
import com.memoecho.eventcenter.service.ConversationDigestBatchService;
import com.memoecho.eventcenter.service.AgentRuntimeDispatchClient;
import com.memoecho.eventcenter.service.EventCenterApplicationService;
import com.memoecho.eventcenter.dto.ConversationDigestBatchResponse;
import jakarta.validation.Valid;
import org.springframework.http.HttpStatus;
import org.springframework.http.ResponseEntity;
import org.springframework.beans.factory.ObjectProvider;
import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.PathVariable;
import org.springframework.web.bind.annotation.PostMapping;
import org.springframework.web.bind.annotation.RequestBody;
import org.springframework.web.bind.annotation.RequestMapping;
import org.springframework.web.bind.annotation.RequestParam;
import org.springframework.web.bind.annotation.RequestHeader;
import org.springframework.web.bind.annotation.RestController;
import org.springframework.web.server.ResponseStatusException;

@RestController
@RequestMapping("/internal/workspace")
public class InternalWorkspaceController {

    private final WorkspaceBriefingApplicationService workspaceBriefingApplicationService;
    private final WorkspaceInboxApplicationService workspaceInboxApplicationService;
    private final LocalUserContextResolver userContextResolver;
    private final ObjectProvider<ConversationDigestBatchService> digestBatchServiceProvider;
    private final AgentRuntimeDispatchClient agentRuntimeDispatchClient;
    private final EventCenterApplicationService eventCenterApplicationService;

    public InternalWorkspaceController(
            WorkspaceBriefingApplicationService workspaceBriefingApplicationService,
            WorkspaceInboxApplicationService workspaceInboxApplicationService,
            LocalUserContextResolver userContextResolver,
            ObjectProvider<ConversationDigestBatchService> digestBatchServiceProvider,
            AgentRuntimeDispatchClient agentRuntimeDispatchClient,
            EventCenterApplicationService eventCenterApplicationService
    ) {
        // 这个构造函数的作用是注入工作台摘要和收件箱聚合服务，让 Controller 只负责参数接收和返回。
        this.workspaceBriefingApplicationService = workspaceBriefingApplicationService;
        this.workspaceInboxApplicationService = workspaceInboxApplicationService;
        this.userContextResolver = userContextResolver;
        this.digestBatchServiceProvider = digestBatchServiceProvider;
        this.agentRuntimeDispatchClient = agentRuntimeDispatchClient;
        this.eventCenterApplicationService = eventCenterApplicationService;
    }

    @GetMapping("/briefing")
    public ResponseEntity<WorkspaceBriefingResponse> briefing(
            @RequestParam String senderId,
            @RequestParam(required = false) String userName,
            @RequestParam(required = false, defaultValue = "480") Integer lookbackMinutes,
            @RequestParam(required = false, defaultValue = "5") Integer conversationLimit,
            @RequestParam(required = false, defaultValue = "5") Integer taskLimit,
            @RequestParam(required = false, defaultValue = "5") Integer scheduleLimit
    ) {
        // 这个函数的作用是提供前端登录后首页所需的摘要包接口。
        return ResponseEntity.ok(workspaceBriefingApplicationService.buildBriefing(
                userName,
                senderId,
                lookbackMinutes,
                conversationLimit,
                taskLimit,
                scheduleLimit
        ));
    }

    @GetMapping("/inbox")
    public ResponseEntity<WorkspaceInboxResponse> inbox(
            @RequestHeader(name = "Authorization", required = false) String authorization,
            @RequestHeader(name = "X-Memo-Echo-User-Id", defaultValue = "local-user") String userId,
            @RequestParam(required = false) String inboxStatus,
            @RequestParam(required = false, defaultValue = "50") Integer limit
    ) {
        // 这个接口的作用是提供工作台收件箱卡片列表，前端无需自行合并事件、草稿和执行状态。
        String ownerUserId = userContextResolver.resolve(authorization, userId);
        return ResponseEntity.ok(workspaceInboxApplicationService.buildInbox(ownerUserId, inboxStatus, limit));
    }

    /** 返回消息空间使用的真实摘要批次，而不是逐条原始消息。 */
    @GetMapping("/digests")
    public ResponseEntity<java.util.List<ConversationDigestBatchResponse>> digests(
            @RequestHeader(name = "Authorization", required = false) String authorization,
            @RequestHeader(name = "X-Memo-Echo-User-Id", defaultValue = "local-user") String userId,
            @RequestParam(required = false, defaultValue = "50") Integer limit
    ) {
        String ownerUserId = userContextResolver.resolve(authorization, userId);
        ConversationDigestBatchService digestBatchService = digestBatchServiceProvider.getIfAvailable();
        return ResponseEntity.ok(digestBatchService == null
                ? java.util.List.of()
                : digestBatchService.list(ownerUserId, limit == null ? 50 : limit));
    }

    /**
     * 返回当前用户拥有的事件所对应的待审批群操作，不向客户端暴露 Runtime 令牌。
     */
    @GetMapping("/group-operations/{eventId}")
    public ResponseEntity<JsonNode> pendingGroupOperation(
            @RequestHeader(name = "Authorization", required = false) String authorization,
            @RequestHeader(name = "X-Memo-Echo-User-Id", defaultValue = "local-user") String userId,
            @PathVariable String eventId
    ) {
        String ownerUserId = userContextResolver.resolve(authorization, userId);
        requireOwnedEvent(ownerUserId, eventId);
        return ResponseEntity.ok(agentRuntimeDispatchClient.getPendingGroupOperation(eventId));
    }

    /**
     * 对当前用户拥有的事件执行一次群管理审批；确认短语不匹配时 Runtime 会拒绝执行。
     */
    @PostMapping("/group-operations/{eventId}/approve")
    public ResponseEntity<JsonNode> approveGroupOperation(
            @RequestHeader(name = "Authorization", required = false) String authorization,
            @RequestHeader(name = "X-Memo-Echo-User-Id", defaultValue = "local-user") String userId,
            @PathVariable String eventId,
            @Valid @RequestBody GroupOperationConfirmationRequest request
    ) {
        String ownerUserId = userContextResolver.resolve(authorization, userId);
        requireOwnedEvent(ownerUserId, eventId);
        JsonNode result = agentRuntimeDispatchClient.approveGroupOperation(
                eventId,
                request.confirmationText()
        );
        if (result != null && "success".equalsIgnoreCase(result.path("status").asText())) {
            eventCenterApplicationService.markInboxDone(eventId);
        }
        return ResponseEntity.ok(result);
    }

    /** 对高权限代理接口执行对象级鉴权，防止用户猜测事件 ID 后审批其他账户的动作。 */
    private void requireOwnedEvent(String ownerUserId, String eventId) {
        if (!eventCenterApplicationService.isEventOwnedBy(ownerUserId, eventId)) {
            throw new ResponseStatusException(HttpStatus.NOT_FOUND, "事件不存在或不属于当前用户");
        }
    }
}
