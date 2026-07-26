package com.memoecho.eventcenter.controller;

import com.memoecho.eventcenter.dto.SecureAssetResponse;
import com.memoecho.eventcenter.dto.SecureAssetRuntimeResponse;
import com.memoecho.eventcenter.dto.SecureAssetUpsertRequest;
import com.memoecho.eventcenter.service.LocalUserContextResolver;
import com.memoecho.eventcenter.service.SecureAssetApplicationService;
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

/**
 * 安全资产 HTTP 接口；用户管理接口与 Runtime 明文解析接口在同一资源下明确分离。
 */
@RestController
@RequestMapping("/internal/secure-assets")
public class InternalSecureAssetController {

    private final SecureAssetApplicationService applicationService;
    private final LocalUserContextResolver userContextResolver;

    /** 注入应用服务和统一身份解析器，控制器不自行判断所有权。 */
    public InternalSecureAssetController(
            SecureAssetApplicationService applicationService,
            LocalUserContextResolver userContextResolver
    ) {
        this.applicationService = applicationService;
        this.userContextResolver = userContextResolver;
    }

    /** 列出当前登录用户的资产元数据。 */
    @GetMapping
    public ResponseEntity<List<SecureAssetResponse>> listAssets(
            @RequestHeader(name = "Authorization", required = false) String authorization,
            @RequestHeader(name = "X-Memo-Echo-User-Id", defaultValue = "local-user") String userId
    ) {
        return ResponseEntity.ok(applicationService.listAssets(userContextResolver.resolve(authorization, userId)));
    }

    /** 读取当前用户的一条资产元数据，响应不包含正文。 */
    @GetMapping("/{assetId}")
    public ResponseEntity<SecureAssetResponse> getAsset(
            @RequestHeader(name = "Authorization", required = false) String authorization,
            @RequestHeader(name = "X-Memo-Echo-User-Id", defaultValue = "local-user") String userId,
            @PathVariable String assetId
    ) {
        return ResponseEntity.ok(applicationService.getAsset(
                userContextResolver.resolve(authorization, userId), assetId));
    }

    /** 创建资产；请求中的 content 会在应用服务层加密后再落库。 */
    @PostMapping
    public ResponseEntity<SecureAssetResponse> createAsset(
            @RequestHeader(name = "Authorization", required = false) String authorization,
            @RequestHeader(name = "X-Memo-Echo-User-Id", defaultValue = "local-user") String userId,
            @Valid @RequestBody SecureAssetUpsertRequest request
    ) {
        return ResponseEntity.ok(applicationService.createAsset(
                userContextResolver.resolve(authorization, userId), request));
    }

    /** 更新当前用户拥有的资产；content 为 null 时保留已有正文。 */
    @PutMapping("/{assetId}")
    public ResponseEntity<SecureAssetResponse> updateAsset(
            @RequestHeader(name = "Authorization", required = false) String authorization,
            @RequestHeader(name = "X-Memo-Echo-User-Id", defaultValue = "local-user") String userId,
            @PathVariable String assetId,
            @Valid @RequestBody SecureAssetUpsertRequest request
    ) {
        return ResponseEntity.ok(applicationService.updateAsset(
                userContextResolver.resolve(authorization, userId), assetId, request));
    }

    /** 删除当前用户拥有的资产。 */
    @DeleteMapping("/{assetId}")
    public ResponseEntity<Void> deleteAsset(
            @RequestHeader(name = "Authorization", required = false) String authorization,
            @RequestHeader(name = "X-Memo-Echo-User-Id", defaultValue = "local-user") String userId,
            @PathVariable String assetId
    ) {
        applicationService.deleteAsset(userContextResolver.resolve(authorization, userId), assetId);
        return ResponseEntity.noContent().build();
    }

    /**
     * 受信任 Runtime 解析并消费资产正文。
     *
     * <p>该接口不接受桌面 JWT 替代 Runtime Token，防止普通客户端直接读取敏感正文。</p>
     */
    @PostMapping("/{assetId}/resolve")
    public ResponseEntity<SecureAssetRuntimeResponse> resolveAsset(
            @RequestHeader(name = "X-Memo-Echo-Runtime-Token", required = false) String runtimeToken,
            @RequestHeader(name = "X-Memo-Echo-User-Id", required = false) String userId,
            @PathVariable String assetId
    ) {
        String resolvedUserId = userContextResolver.resolveRuntimeUser(runtimeToken, userId);
        return ResponseEntity.ok(applicationService.resolveForRuntime(resolvedUserId, assetId));
    }
}
