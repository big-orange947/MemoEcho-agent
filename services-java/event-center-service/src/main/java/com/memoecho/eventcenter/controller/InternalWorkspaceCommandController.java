package com.memoecho.eventcenter.controller;

import com.memoecho.eventcenter.dto.WorkspaceCommandRequest;
import com.memoecho.eventcenter.dto.WorkspaceCommandResponse;
import com.memoecho.eventcenter.service.LocalUserContextResolver;
import com.memoecho.eventcenter.service.WorkspaceCommandApplicationService;
import jakarta.validation.Valid;
import org.springframework.http.ResponseEntity;
import org.springframework.web.bind.annotation.PostMapping;
import org.springframework.web.bind.annotation.RequestBody;
import org.springframework.web.bind.annotation.RequestHeader;
import org.springframework.web.bind.annotation.RequestMapping;
import org.springframework.web.bind.annotation.RestController;

@RestController
@RequestMapping("/internal/workspace/commands")
public class InternalWorkspaceCommandController {

    private final WorkspaceCommandApplicationService applicationService;
    private final LocalUserContextResolver userContextResolver;

    /**
     * 注入命令服务和用户解析器，确保桌面端不能伪造其他用户执行 Agent。
     */
    public InternalWorkspaceCommandController(
            WorkspaceCommandApplicationService applicationService,
            LocalUserContextResolver userContextResolver
    ) {
        this.applicationService = applicationService;
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
}
