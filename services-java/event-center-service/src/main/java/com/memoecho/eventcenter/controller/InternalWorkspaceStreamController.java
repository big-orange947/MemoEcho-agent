package com.memoecho.eventcenter.controller;

import com.memoecho.eventcenter.service.WorkspaceEventStreamService;
import org.springframework.http.MediaType;
import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.RequestMapping;
import org.springframework.web.bind.annotation.RequestParam;
import org.springframework.web.bind.annotation.RestController;
import org.springframework.web.servlet.mvc.method.annotation.SseEmitter;

@RestController
@RequestMapping("/internal/workspace")
public class InternalWorkspaceStreamController {

    private final WorkspaceEventStreamService workspaceEventStreamService;

    public InternalWorkspaceStreamController(WorkspaceEventStreamService workspaceEventStreamService) {
        // 这个构造函数的作用是注入 SSE 广播服务，Controller 只负责建立 HTTP 流连接。
        this.workspaceEventStreamService = workspaceEventStreamService;
    }

    @GetMapping(value = "/stream", produces = MediaType.TEXT_EVENT_STREAM_VALUE)
    public SseEmitter stream(@RequestParam String platform, @RequestParam String accountId) {
        // 这个接口的作用是让工作台订阅指定平台账号的实时更新，不返回其他账号的数据。
        return workspaceEventStreamService.subscribe(platform, accountId);
    }
}
