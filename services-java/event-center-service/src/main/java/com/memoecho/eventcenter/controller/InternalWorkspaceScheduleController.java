package com.memoecho.eventcenter.controller;

import com.memoecho.eventcenter.dto.ScheduleServiceScheduleResponse;
import com.memoecho.eventcenter.dto.WorkspaceScheduleCreateRequest;
import com.memoecho.eventcenter.dto.WorkspaceScheduleSourceContextResponse;
import com.memoecho.eventcenter.service.LocalUserContextResolver;
import com.memoecho.eventcenter.service.WorkspaceScheduleApplicationService;
import jakarta.validation.Valid;
import org.springframework.http.ResponseEntity;
import org.springframework.web.bind.annotation.DeleteMapping;
import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.PathVariable;
import org.springframework.web.bind.annotation.PostMapping;
import org.springframework.web.bind.annotation.RequestBody;
import org.springframework.web.bind.annotation.RequestHeader;
import org.springframework.web.bind.annotation.RequestMapping;
import org.springframework.web.bind.annotation.RequestParam;
import org.springframework.web.bind.annotation.RestController;

@RestController
@RequestMapping("/internal/workspace/schedules")
public class InternalWorkspaceScheduleController {

    private final WorkspaceScheduleApplicationService scheduleApplicationService;
    private final LocalUserContextResolver userContextResolver;

    public InternalWorkspaceScheduleController(
            WorkspaceScheduleApplicationService scheduleApplicationService,
            LocalUserContextResolver userContextResolver
    ) {
        // 这个构造函数的作用是注入日程业务服务和用户解析器，使所有工作台日程写操作都经过登录鉴权。
        this.scheduleApplicationService = scheduleApplicationService;
        this.userContextResolver = userContextResolver;
    }

    @PostMapping
    public ResponseEntity<ScheduleServiceScheduleResponse> createSchedule(
            @RequestHeader(name = "Authorization", required = false) String authorization,
            @RequestHeader(name = "X-Memo-Echo-User-Id", defaultValue = "local-user") String userId,
            @Valid @RequestBody WorkspaceScheduleCreateRequest request
    ) {
        // 这个函数的作用是接收客户端手动创建请求，并把可信用户 ID 交给业务层生成来源标识。
        String ownerUserId = userContextResolver.resolve(authorization, userId);
        return ResponseEntity.ok(scheduleApplicationService.createManualSchedule(ownerUserId, request));
    }

    @DeleteMapping("/{scheduleId}")
    public ResponseEntity<Void> deleteSchedule(
            @RequestHeader(name = "Authorization", required = false) String authorization,
            @RequestHeader(name = "X-Memo-Echo-User-Id", defaultValue = "local-user") String userId,
            @PathVariable String scheduleId
    ) {
        // 这个函数的作用是删除当前用户拥有的日程，成功后返回无内容响应。
        String ownerUserId = userContextResolver.resolve(authorization, userId);
        scheduleApplicationService.deleteSchedule(ownerUserId, scheduleId);
        return ResponseEntity.noContent().build();
    }

    @GetMapping("/{scheduleId}/source-context")
    public ResponseEntity<WorkspaceScheduleSourceContextResponse> sourceContext(
            @RequestHeader(name = "Authorization", required = false) String authorization,
            @RequestHeader(name = "X-Memo-Echo-User-Id", defaultValue = "local-user") String userId,
            @PathVariable String scheduleId,
            @RequestParam(required = false, defaultValue = "3") Integer radius
    ) {
        // 这个函数的作用是按需加载日程来源上下文，避免消息空间首屏读取大量聊天历史。
        String ownerUserId = userContextResolver.resolve(authorization, userId);
        return ResponseEntity.ok(scheduleApplicationService.getSourceContext(ownerUserId, scheduleId, radius));
    }
}
