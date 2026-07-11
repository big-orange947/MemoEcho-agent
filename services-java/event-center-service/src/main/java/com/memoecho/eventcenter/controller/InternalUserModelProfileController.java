package com.memoecho.eventcenter.controller;

import com.memoecho.eventcenter.dto.UserModelProfileResolveRequest;
import com.memoecho.eventcenter.dto.UserModelProfileResolveResponse;
import com.memoecho.eventcenter.dto.UserModelProfileResponse;
import com.memoecho.eventcenter.dto.UserModelProfileUpsertRequest;
import com.memoecho.eventcenter.service.LocalUserContextResolver;
import com.memoecho.eventcenter.service.UserModelProfileApplicationService;
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
@RequestMapping("/internal/user-model-profiles")
public class InternalUserModelProfileController {

    private final UserModelProfileApplicationService applicationService;
    private final LocalUserContextResolver userContextResolver;

    /**
     * 注入模型配置服务和当前用户解析器，控制器不直接处理密钥或令牌内容。
     */
    public InternalUserModelProfileController(
            UserModelProfileApplicationService applicationService,
            LocalUserContextResolver userContextResolver
    ) {
        this.applicationService = applicationService;
        this.userContextResolver = userContextResolver;
    }

    /**
     * 返回当前用户自己的模型配置列表，JWT 存在时优先以 JWT 身份为准。
     */
    @GetMapping
    public ResponseEntity<List<UserModelProfileResponse>> listProfiles(
            @RequestHeader(name = "Authorization", required = false) String authorization,
            @RequestHeader(name = "X-Memo-Echo-User-Id", defaultValue = "local-user") String userId
    ) {
        return ResponseEntity.ok(applicationService.listProfiles(userContextResolver.resolve(authorization, userId)));
    }

    /**
     * 查询当前用户拥有的一条配置，配置不属于当前用户时返回 404。
     */
    @GetMapping("/{profileId}")
    public ResponseEntity<UserModelProfileResponse> getProfile(
            @RequestHeader(name = "Authorization", required = false) String authorization,
            @RequestHeader(name = "X-Memo-Echo-User-Id", defaultValue = "local-user") String userId,
            @PathVariable String profileId
    ) {
        return ResponseEntity.ok(applicationService.getProfile(
                userContextResolver.resolve(authorization, userId), profileId));
    }

    /**
     * 创建模型配置，并将其归属强制绑定为当前用户而不是请求体中的 userId。
     */
    @PostMapping
    public ResponseEntity<UserModelProfileResponse> createProfile(
            @RequestHeader(name = "Authorization", required = false) String authorization,
            @RequestHeader(name = "X-Memo-Echo-User-Id", defaultValue = "local-user") String userId,
            @Valid @RequestBody UserModelProfileUpsertRequest request
    ) {
        return ResponseEntity.ok(applicationService.createProfile(
                userContextResolver.resolve(authorization, userId), request));
    }

    /**
     * 更新当前用户拥有的配置，更新请求不能更改配置所有者。
     */
    @PutMapping("/{profileId}")
    public ResponseEntity<UserModelProfileResponse> updateProfile(
            @RequestHeader(name = "Authorization", required = false) String authorization,
            @RequestHeader(name = "X-Memo-Echo-User-Id", defaultValue = "local-user") String userId,
            @PathVariable String profileId,
            @Valid @RequestBody UserModelProfileUpsertRequest request
    ) {
        return ResponseEntity.ok(applicationService.updateProfile(
                userContextResolver.resolve(authorization, userId), profileId, request));
    }

    /**
     * 删除当前用户拥有的配置，其他用户的配置 id 不会生效。
     */
    @DeleteMapping("/{profileId}")
    public ResponseEntity<Void> deleteProfile(
            @RequestHeader(name = "Authorization", required = false) String authorization,
            @RequestHeader(name = "X-Memo-Echo-User-Id", defaultValue = "local-user") String userId,
            @PathVariable String profileId
    ) {
        applicationService.deleteProfile(userContextResolver.resolve(authorization, userId), profileId);
        return ResponseEntity.noContent().build();
    }

    /**
     * 为运行时 route 解析模型；携带 JWT 时忽略请求体 userId，旧运行时仍可暂用 userId 兼容调用。
     */
    @PostMapping("/resolve")
    public ResponseEntity<UserModelProfileResolveResponse> resolveProfile(
            @RequestHeader(name = "Authorization", required = false) String authorization,
            @RequestHeader(name = "X-Memo-Echo-User-Id", required = false) String userId,
            @RequestHeader(name = "X-Memo-Echo-Runtime-Token", required = false) String runtimeToken,
            @Valid @RequestBody UserModelProfileResolveRequest request
    ) {
        String requestedUserId = userId == null || userId.isBlank() ? request.userId() : userId;
        String resolvedUserId = authorization != null && !authorization.isBlank()
                ? userContextResolver.resolve(authorization, requestedUserId)
                : runtimeToken != null && !runtimeToken.isBlank()
                ? userContextResolver.resolveRuntimeUser(runtimeToken, requestedUserId)
                : userContextResolver.resolve(authorization, requestedUserId);
        return ResponseEntity.ok(applicationService.resolveProfile(
                resolvedUserId, request));
    }
}
