package com.memoecho.eventcenter.controller;

import com.memoecho.eventcenter.dto.ConversationProfileConfigurationResponse;
import com.memoecho.eventcenter.dto.ConversationProfileMatchRequest;
import com.memoecho.eventcenter.dto.ConversationProfileMatchResponse;
import com.memoecho.eventcenter.dto.ConversationProfileResponse;
import com.memoecho.eventcenter.dto.ConversationProfileUpsertRequest;
import com.memoecho.eventcenter.dto.ConversationAgentStateRequest;
import com.memoecho.eventcenter.dto.ConversationProxyTaskCompletionRequest;
import com.memoecho.eventcenter.dto.ConversationProxyTaskDecisionRequest;
import com.memoecho.eventcenter.dto.ConversationProxyTaskStateResponse;
import com.memoecho.eventcenter.service.ConversationProfileApplicationService;
import com.memoecho.eventcenter.service.ConversationProxyTaskStateService;
import com.memoecho.eventcenter.service.LocalUserContextResolver;
import com.memoecho.eventcenter.service.SkillCatalogApplicationService;
import jakarta.validation.Valid;
import org.springframework.http.ResponseEntity;
import org.springframework.web.bind.annotation.DeleteMapping;
import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.PathVariable;
import org.springframework.web.bind.annotation.PostMapping;
import org.springframework.web.bind.annotation.PutMapping;
import org.springframework.web.bind.annotation.RequestBody;
import org.springframework.web.bind.annotation.RequestHeader;
import org.springframework.web.bind.annotation.RequestMapping;
import org.springframework.web.bind.annotation.RestController;

import java.util.List;

@RestController
@RequestMapping("/internal/conversation-profiles")
public class InternalConversationProfileController {

    private final ConversationProfileApplicationService applicationService;
    private final SkillCatalogApplicationService skillCatalogApplicationService;
    private final LocalUserContextResolver userContextResolver;
    private final ConversationProxyTaskStateService taskStateService;

    public InternalConversationProfileController(
            ConversationProfileApplicationService applicationService,
            SkillCatalogApplicationService skillCatalogApplicationService,
            LocalUserContextResolver userContextResolver,
            ConversationProxyTaskStateService taskStateService
    ) {
        // 这个构造函数的作用是同时注入会话设定主服务和配置辅助服务，便于前端一次拿到可选 skill、route 与工具列表。
        this.applicationService = applicationService;
        this.skillCatalogApplicationService = skillCatalogApplicationService;
        this.userContextResolver = userContextResolver;
        this.taskStateService = taskStateService;
    }

    @GetMapping
    public ResponseEntity<List<ConversationProfileResponse>> listProfiles(
            @RequestHeader(name = "Authorization", required = false) String authorization,
            @RequestHeader(name = "X-Memo-Echo-User-Id", defaultValue = "local-user") String userId
    ) {
        return ResponseEntity.ok(applicationService.listProfiles(userContextResolver.resolve(authorization, userId)));
    }

    @GetMapping("/configuration")
    public ResponseEntity<ConversationProfileConfigurationResponse> getConfiguration() {
        // 这个函数的作用是返回会话设定页面所需的全部配置元数据，避免前端自己硬编码枚举和值域。
        return ResponseEntity.ok(skillCatalogApplicationService.buildConversationProfileConfiguration());
    }

    @GetMapping("/{profileId}")
    public ResponseEntity<ConversationProfileResponse> getProfile(
            @RequestHeader(name = "Authorization", required = false) String authorization,
            @RequestHeader(name = "X-Memo-Echo-User-Id", defaultValue = "local-user") String userId,
            @PathVariable String profileId
    ) {
        return ResponseEntity.ok(applicationService.getProfile(userContextResolver.resolve(authorization, userId), profileId));
    }

    @PostMapping
    public ResponseEntity<ConversationProfileResponse> createProfile(
            @RequestHeader(name = "Authorization", required = false) String authorization,
            @RequestHeader(name = "X-Memo-Echo-User-Id", defaultValue = "local-user") String userId,
            @Valid @RequestBody ConversationProfileUpsertRequest request
    ) {
        return ResponseEntity.ok(applicationService.createProfile(userContextResolver.resolve(authorization, userId), request));
    }

    @PutMapping("/{profileId}")
    public ResponseEntity<ConversationProfileResponse> updateProfile(
            @RequestHeader(name = "Authorization", required = false) String authorization,
            @RequestHeader(name = "X-Memo-Echo-User-Id", defaultValue = "local-user") String userId,
            @PathVariable String profileId,
            @Valid @RequestBody ConversationProfileUpsertRequest request
    ) {
        return ResponseEntity.ok(applicationService.updateProfile(userContextResolver.resolve(authorization, userId), profileId, request));
    }

    @DeleteMapping("/{profileId}")
    public ResponseEntity<Void> deleteProfile(
            @RequestHeader(name = "Authorization", required = false) String authorization,
            @RequestHeader(name = "X-Memo-Echo-User-Id", defaultValue = "local-user") String userId,
            @PathVariable String profileId
    ) {
        applicationService.deleteProfile(userContextResolver.resolve(authorization, userId), profileId);
        return ResponseEntity.noContent().build();
    }

    @PostMapping("/match")
    public ResponseEntity<ConversationProfileMatchResponse> matchProfile(
            @RequestHeader(name = "Authorization", required = false) String authorization,
            @RequestHeader(name = "X-Memo-Echo-User-Id", required = false) String userId,
            @RequestHeader(name = "X-Memo-Echo-Runtime-Token", required = false) String runtimeToken,
            @Valid @RequestBody ConversationProfileMatchRequest request
    ) {
        String requestedUserId = userId == null || userId.isBlank() ? "default" : userId;
        String resolvedUserId = authorization != null && !authorization.isBlank()
                ? userContextResolver.resolve(authorization, requestedUserId)
                : runtimeToken != null && !runtimeToken.isBlank()
                ? userContextResolver.resolveRuntimeUser(runtimeToken, requestedUserId)
                : userContextResolver.resolve(authorization, requestedUserId);
        return ResponseEntity.ok(applicationService.matchProfile(resolvedUserId, request));
    }

    /** 人工发送完成后，根据用户选择继续或暂停当前会话代理。 */
    @PostMapping("/agent-state")
    public ResponseEntity<java.util.Map<String, Object>> updateAgentState(
            @RequestHeader(name = "Authorization", required = false) String authorization,
            @RequestHeader(name = "X-Memo-Echo-User-Id", defaultValue = "local-user") String userId,
            @Valid @RequestBody ConversationAgentStateRequest request
    ) {
        int updated = applicationService.updateConversationAgentState(
                userContextResolver.resolve(authorization, userId), request.platform(), request.chatType(),
                request.chatId(), request.continueAgent());
        return ResponseEntity.ok(java.util.Map.of("continueAgent", request.continueAgent(), "updatedProfiles", updated));
    }

    /** Runtime 判断任务已达成后提交结束代理申请，申请本身不会立即停用代理。 */
    @PostMapping("/{profileId}/task-completion/request")
    public ResponseEntity<ConversationProxyTaskStateResponse> requestTaskCompletion(
            @RequestHeader(name = "X-Memo-Echo-User-Id", required = false) String userId,
            @RequestHeader(name = "X-Memo-Echo-Runtime-Token", required = false) String runtimeToken,
            @PathVariable String profileId,
            @RequestBody ConversationProxyTaskCompletionRequest request
    ) {
        String requestedUserId = userId == null || userId.isBlank() ? "default" : userId;
        String resolvedUserId = userContextResolver.resolveRuntimeUser(runtimeToken, requestedUserId);
        return ResponseEntity.ok(taskStateService.requestCompletion(
                resolvedUserId, profileId, request.chatId(), request.summary(), request.reason(), request.evidence()
        ));
    }

    /** 返回当前用户所有等待审批的结束代理申请。 */
    @GetMapping("/task-completion/pending")
    public ResponseEntity<List<ConversationProxyTaskStateResponse>> listPendingTaskCompletions(
            @RequestHeader(name = "Authorization", required = false) String authorization,
            @RequestHeader(name = "X-Memo-Echo-User-Id", defaultValue = "local-user") String userId
    ) {
        String resolvedUserId = userContextResolver.resolve(authorization, userId);
        return ResponseEntity.ok(taskStateService.listPending(resolvedUserId));
    }

    /** 用户批准后正式停止该会话代理，拒绝则恢复任务推进。 */
    @PostMapping("/{profileId}/task-completion/decision")
    public ResponseEntity<ConversationProxyTaskStateResponse> decideTaskCompletion(
            @RequestHeader(name = "Authorization", required = false) String authorization,
            @RequestHeader(name = "X-Memo-Echo-User-Id", defaultValue = "local-user") String userId,
            @PathVariable String profileId,
            @RequestBody ConversationProxyTaskDecisionRequest request
    ) {
        String resolvedUserId = userContextResolver.resolve(authorization, userId);
        return ResponseEntity.ok(taskStateService.decide(
                resolvedUserId, profileId, request.chatId(), request.approved()
        ));
    }
}
