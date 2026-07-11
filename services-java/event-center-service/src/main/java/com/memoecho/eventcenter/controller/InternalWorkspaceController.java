package com.memoecho.eventcenter.controller;

import com.memoecho.eventcenter.dto.WorkspaceBriefingResponse;
import com.memoecho.eventcenter.dto.WorkspaceInboxResponse;
import com.memoecho.eventcenter.service.WorkspaceBriefingApplicationService;
import com.memoecho.eventcenter.service.WorkspaceInboxApplicationService;
import org.springframework.http.ResponseEntity;
import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.RequestMapping;
import org.springframework.web.bind.annotation.RequestParam;
import org.springframework.web.bind.annotation.RestController;

@RestController
@RequestMapping("/internal/workspace")
public class InternalWorkspaceController {

    private final WorkspaceBriefingApplicationService workspaceBriefingApplicationService;
    private final WorkspaceInboxApplicationService workspaceInboxApplicationService;

    public InternalWorkspaceController(
            WorkspaceBriefingApplicationService workspaceBriefingApplicationService,
            WorkspaceInboxApplicationService workspaceInboxApplicationService
    ) {
        // 这个构造函数的作用是注入工作台摘要和收件箱聚合服务，让 Controller 只负责参数接收和返回。
        this.workspaceBriefingApplicationService = workspaceBriefingApplicationService;
        this.workspaceInboxApplicationService = workspaceInboxApplicationService;
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
            @RequestParam(required = false) String inboxStatus,
            @RequestParam(required = false, defaultValue = "50") Integer limit
    ) {
        // 这个接口的作用是提供工作台收件箱卡片列表，前端无需自行合并事件、草稿和执行状态。
        return ResponseEntity.ok(workspaceInboxApplicationService.buildInbox(inboxStatus, limit));
    }
}
