package com.memoecho.eventcenter.controller;

import com.memoecho.eventcenter.dto.MemoryCandidateRejectRequest;
import com.memoecho.eventcenter.dto.MemoryCandidateEvidenceResponse;
import com.memoecho.eventcenter.dto.MemoryCandidateResponse;
import com.memoecho.eventcenter.dto.MemoryCandidateUpsertRequest;
import com.memoecho.eventcenter.dto.MemoryConflictResolutionRequest;
import com.memoecho.eventcenter.dto.MemoryConflictResolutionResponse;
import com.memoecho.eventcenter.service.LocalUserContextResolver;
import com.memoecho.eventcenter.service.MemoryCandidateApplicationService;
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
import org.springframework.web.bind.annotation.RequestParam;
import org.springframework.web.bind.annotation.RestController;

import java.util.List;

/** 长期记忆候选接口，明确分离桌面端管理权限与 Runtime 服务权限。 */
@RestController
@RequestMapping("/internal/memories")
public class InternalMemoryCandidateController {

    private final MemoryCandidateApplicationService applicationService;
    private final LocalUserContextResolver userContextResolver;

    /** 注入应用服务和统一身份解析器。 */
    public InternalMemoryCandidateController(
            MemoryCandidateApplicationService applicationService,
            LocalUserContextResolver userContextResolver
    ) {
        this.applicationService = applicationService;
        this.userContextResolver = userContextResolver;
    }

    /** 列出当前登录用户的长期记忆，可按状态筛选。 */
    @GetMapping
    public ResponseEntity<List<MemoryCandidateResponse>> list(
            @RequestHeader(name = "Authorization", required = false) String authorization,
            @RequestHeader(name = "X-Memo-Echo-User-Id", defaultValue = "local-user") String userId,
            @RequestParam(name = "status", required = false) String status
    ) {
        String resolvedUserId = userContextResolver.resolve(authorization, userId);
        return ResponseEntity.ok(applicationService.list(resolvedUserId, status));
    }

    /** 创建一条用户手工候选。 */
    @PostMapping
    public ResponseEntity<MemoryCandidateResponse> create(
            @RequestHeader(name = "Authorization", required = false) String authorization,
            @RequestHeader(name = "X-Memo-Echo-User-Id", defaultValue = "local-user") String userId,
            @Valid @RequestBody MemoryCandidateUpsertRequest request
    ) {
        String resolvedUserId = userContextResolver.resolve(authorization, userId);
        return ResponseEntity.ok(applicationService.create(resolvedUserId, request));
    }

    /** 编辑仍处于候选状态的结构化事实。 */
    @PutMapping("/{id}")
    public ResponseEntity<MemoryCandidateResponse> update(
            @RequestHeader(name = "Authorization", required = false) String authorization,
            @RequestHeader(name = "X-Memo-Echo-User-Id", defaultValue = "local-user") String userId,
            @PathVariable String id,
            @Valid @RequestBody MemoryCandidateUpsertRequest request
    ) {
        String resolvedUserId = userContextResolver.resolve(authorization, userId);
        return ResponseEntity.ok(applicationService.update(resolvedUserId, id, request));
    }

    /** 用户确认候选事实，使其可以被 Runtime 读取。 */
    @PostMapping("/{id}/verify")
    public ResponseEntity<MemoryCandidateResponse> verify(
            @RequestHeader(name = "Authorization", required = false) String authorization,
            @RequestHeader(name = "X-Memo-Echo-User-Id", defaultValue = "local-user") String userId,
            @PathVariable String id
    ) {
        String resolvedUserId = userContextResolver.resolve(authorization, userId);
        return ResponseEntity.ok(applicationService.verify(resolvedUserId, id));
    }

    /** 用户拒绝候选事实，使其永久退出 Runtime 上下文。 */
    @PostMapping("/{id}/reject")
    public ResponseEntity<MemoryCandidateResponse> reject(
            @RequestHeader(name = "Authorization", required = false) String authorization,
            @RequestHeader(name = "X-Memo-Echo-User-Id", defaultValue = "local-user") String userId,
            @PathVariable String id,
            @Valid @RequestBody(required = false) MemoryCandidateRejectRequest request
    ) {
        String resolvedUserId = userContextResolver.resolve(authorization, userId);
        return ResponseEntity.ok(applicationService.reject(resolvedUserId, id, request));
    }

    /** 按需读取候选来源消息及其相邻上下文，不在记忆列表首屏暴露完整聊天历史。 */
    @GetMapping("/{id}/evidence")
    public ResponseEntity<MemoryCandidateEvidenceResponse> evidence(
            @RequestHeader(name = "Authorization", required = false) String authorization,
            @RequestHeader(name = "X-Memo-Echo-User-Id", defaultValue = "local-user") String userId,
            @PathVariable String id,
            @RequestParam(name = "radius", defaultValue = "3") Integer radius
    ) {
        String resolvedUserId = userContextResolver.resolve(authorization, userId);
        return ResponseEntity.ok(applicationService.evidence(resolvedUserId, id, radius));
    }

    /** 原子处理候选与已确认事实的冲突，避免客户端多次调用造成中间不一致。 */
    @PostMapping("/{id}/resolve-conflict")
    public ResponseEntity<MemoryConflictResolutionResponse> resolveConflict(
            @RequestHeader(name = "Authorization", required = false) String authorization,
            @RequestHeader(name = "X-Memo-Echo-User-Id", defaultValue = "local-user") String userId,
            @PathVariable String id,
            @Valid @RequestBody MemoryConflictResolutionRequest request
    ) {
        String resolvedUserId = userContextResolver.resolve(authorization, userId);
        return ResponseEntity.ok(applicationService.resolveConflict(resolvedUserId, id, request));
    }

    /** 删除当前用户拥有的一条长期记忆。 */
    @DeleteMapping("/{id}")
    public ResponseEntity<Void> delete(
            @RequestHeader(name = "Authorization", required = false) String authorization,
            @RequestHeader(name = "X-Memo-Echo-User-Id", defaultValue = "local-user") String userId,
            @PathVariable String id
    ) {
        String resolvedUserId = userContextResolver.resolve(authorization, userId);
        applicationService.delete(resolvedUserId, id);
        return ResponseEntity.noContent().build();
    }

    /** Runtime 提交从账号主人消息中抽取的候选，服务端会再次校验来源身份。 */
    @PostMapping("/runtime/candidates")
    public ResponseEntity<MemoryCandidateResponse> createFromRuntime(
            @RequestHeader(name = "X-Memo-Echo-Runtime-Token", required = false) String runtimeToken,
            @RequestHeader(name = "X-Memo-Echo-User-Id", required = false) String userId,
            @Valid @RequestBody MemoryCandidateUpsertRequest request
    ) {
        String resolvedUserId = userContextResolver.resolveRuntimeUser(runtimeToken, userId);
        return ResponseEntity.ok(applicationService.createFromRuntime(resolvedUserId, request));
    }

    /** Runtime 读取当前会话可用的已确认记忆；该接口不会返回候选和拒绝记录。 */
    @GetMapping("/runtime/verified")
    public ResponseEntity<List<MemoryCandidateResponse>> listVerifiedForRuntime(
            @RequestHeader(name = "X-Memo-Echo-Runtime-Token", required = false) String runtimeToken,
            @RequestHeader(name = "X-Memo-Echo-User-Id", required = false) String userId,
            @RequestParam(name = "platform", required = false) String platform,
            @RequestParam(name = "scene", required = false) String scene,
            @RequestParam(name = "chatType", required = false) String chatType,
            @RequestParam(name = "chatId", required = false) String chatId
    ) {
        String resolvedUserId = userContextResolver.resolveRuntimeUser(runtimeToken, userId);
        return ResponseEntity.ok(applicationService.listVerifiedForRuntime(
                resolvedUserId, platform, scene, chatType, chatId));
    }
}
