package com.memoecho.eventcenter.controller;

import com.memoecho.eventcenter.dto.PlatformConnectionResponse;
import com.memoecho.eventcenter.dto.PlatformConnectionUpsertRequest;
import com.memoecho.eventcenter.service.PlatformConnectionApplicationService;
import com.memoecho.eventcenter.service.LocalUserContextResolver;
import jakarta.validation.Valid;
import org.springframework.http.HttpStatus;
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
@RequestMapping("/internal/connections")
public class InternalPlatformConnectionController {

    private final PlatformConnectionApplicationService applicationService;
    private final LocalUserContextResolver userContextResolver;

    public InternalPlatformConnectionController(
            PlatformConnectionApplicationService applicationService,
            LocalUserContextResolver userContextResolver
    ) {
        // 这个构造函数的作用是注入连接状态查询服务，使 Controller 不接触平台凭据。
        this.applicationService = applicationService;
        this.userContextResolver = userContextResolver;
    }

    @GetMapping
    public ResponseEntity<List<PlatformConnectionResponse>> list(
            @RequestHeader(name = "Authorization", required = false) String authorization,
            @RequestHeader(name = "X-Memo-Echo-User-Id", defaultValue = "local-user") String userId
    ) {
        // 这个接口的作用是为工作台返回已接入平台的连接和健康状态。
        return ResponseEntity.ok(applicationService.listConnections(userContextResolver.resolve(authorization, userId)));
    }

    @PostMapping
    public ResponseEntity<PlatformConnectionResponse> create(
            @RequestHeader(name = "Authorization", required = false) String authorization,
            @RequestHeader(name = "X-Memo-Echo-User-Id", defaultValue = "local-user") String userId,
            @Valid @RequestBody PlatformConnectionUpsertRequest request
    ) {
        // 这个接口的作用是为当前用户创建连接档案，credential 字段只写不回显。
        return ResponseEntity.status(HttpStatus.CREATED)
                .body(applicationService.create(userContextResolver.resolve(authorization, userId), request));
    }

    @PutMapping("/{connectionId}")
    public ResponseEntity<PlatformConnectionResponse> update(
            @RequestHeader(name = "Authorization", required = false) String authorization,
            @RequestHeader(name = "X-Memo-Echo-User-Id", defaultValue = "local-user") String userId,
            @PathVariable String connectionId,
            @Valid @RequestBody PlatformConnectionUpsertRequest request
    ) {
        // 这个接口的作用是更新当前用户拥有的连接配置。
        return ResponseEntity.ok(applicationService.update(
                userContextResolver.resolve(authorization, userId), connectionId, request));
    }

    @PostMapping("/{connectionId}/health")
    public ResponseEntity<PlatformConnectionResponse> health(
            @RequestHeader(name = "Authorization", required = false) String authorization,
            @RequestHeader(name = "X-Memo-Echo-User-Id", defaultValue = "local-user") String userId,
            @PathVariable String connectionId
    ) {
        // 这个接口的作用是主动刷新连接的登录账号与健康状态。
        return ResponseEntity.ok(applicationService.checkHealth(
                userContextResolver.resolve(authorization, userId), connectionId));
    }

    @DeleteMapping("/{connectionId}")
    public ResponseEntity<Void> delete(
            @RequestHeader(name = "Authorization", required = false) String authorization,
            @RequestHeader(name = "X-Memo-Echo-User-Id", defaultValue = "local-user") String userId,
            @PathVariable String connectionId
    ) {
        // 这个接口的作用是删除当前用户拥有的连接及加密凭据。
        applicationService.delete(userContextResolver.resolve(authorization, userId), connectionId);
        return ResponseEntity.noContent().build();
    }
}
