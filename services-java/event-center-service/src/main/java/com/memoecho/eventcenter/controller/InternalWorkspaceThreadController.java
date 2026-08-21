package com.memoecho.eventcenter.controller;

import com.memoecho.eventcenter.dto.WorkspaceThreadMessageResponse;
import com.memoecho.eventcenter.dto.WorkspaceThreadMessageSendResponse;
import com.memoecho.eventcenter.dto.WorkspaceThreadResponse;
import com.memoecho.eventcenter.service.LocalUserContextResolver;
import com.memoecho.eventcenter.service.WorkspaceThreadApplicationService;
import jakarta.validation.Valid;
import jakarta.validation.constraints.NotBlank;
import jakarta.validation.constraints.Size;
import org.springframework.http.ResponseEntity;
import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.PatchMapping;
import org.springframework.web.bind.annotation.PathVariable;
import org.springframework.web.bind.annotation.PostMapping;
import org.springframework.web.bind.annotation.RequestBody;
import org.springframework.web.bind.annotation.RequestHeader;
import org.springframework.web.bind.annotation.RequestMapping;
import org.springframework.web.bind.annotation.RequestParam;
import org.springframework.web.bind.annotation.RestController;

import java.util.List;

/**
 * 主控台对话式工作区：线程与消息接口。
 *
 * 鉴权与旧的工作台命令接口完全一致（JWT 或本地联调 legacy 用户头）。
 * P1 中发消息同步执行整条命令链路，符合设计文档的分期约定。
 */
@RestController
@RequestMapping("/internal/workspace/threads")
public class InternalWorkspaceThreadController {

    private final WorkspaceThreadApplicationService threadService;
    private final LocalUserContextResolver userContextResolver;

    public InternalWorkspaceThreadController(
            WorkspaceThreadApplicationService threadService,
            LocalUserContextResolver userContextResolver
    ) {
        this.threadService = threadService;
        this.userContextResolver = userContextResolver;
    }

    /** 新建对话线程，标题可选。 */
    @PostMapping
    public ResponseEntity<WorkspaceThreadResponse> createThread(
            @RequestHeader(name = "Authorization", required = false) String authorization,
            @RequestHeader(name = "X-Memo-Echo-User-Id", defaultValue = "local-user") String userId,
            @Valid @RequestBody(required = false) CreateThreadRequest request
    ) {
        String resolvedUserId = userContextResolver.resolve(authorization, userId);
        String title = request == null ? "" : request.title();
        return ResponseEntity.ok(threadService.createThread(resolvedUserId, title));
    }

    /** 列出线程；includeArchived=true 时包含归档。 */
    @GetMapping
    public ResponseEntity<List<WorkspaceThreadResponse>> listThreads(
            @RequestHeader(name = "Authorization", required = false) String authorization,
            @RequestHeader(name = "X-Memo-Echo-User-Id", defaultValue = "local-user") String userId,
            @RequestParam(defaultValue = "false") boolean includeArchived
    ) {
        String resolvedUserId = userContextResolver.resolve(authorization, userId);
        return ResponseEntity.ok(threadService.listThreads(resolvedUserId, includeArchived));
    }

    /** 重命名 / 置顶 / 归档线程。 */
    @PatchMapping("/{threadId}")
    public ResponseEntity<WorkspaceThreadResponse> updateThread(
            @RequestHeader(name = "Authorization", required = false) String authorization,
            @RequestHeader(name = "X-Memo-Echo-User-Id", defaultValue = "local-user") String userId,
            @PathVariable String threadId,
            @RequestBody UpdateThreadRequest request
    ) {
        String resolvedUserId = userContextResolver.resolve(authorization, userId);
        return ResponseEntity.ok(threadService.updateThread(
                resolvedUserId, threadId, request.title(), request.pinned(), request.archived()));
    }

    /** 分页读取线程内消息，按时间倒序。 */
    @GetMapping("/{threadId}/messages")
    public ResponseEntity<List<WorkspaceThreadMessageResponse>> listMessages(
            @RequestHeader(name = "Authorization", required = false) String authorization,
            @RequestHeader(name = "X-Memo-Echo-User-Id", defaultValue = "local-user") String userId,
            @PathVariable String threadId,
            @RequestParam(defaultValue = "50") int limit,
            @RequestParam(required = false) String before
    ) {
        String resolvedUserId = userContextResolver.resolve(authorization, userId);
        return ResponseEntity.ok(threadService.listMessages(resolvedUserId, threadId, limit, before));
    }

    /** 读取单条消息（含解析后的执行结果）。 */
    @GetMapping("/{threadId}/messages/{messageId}")
    public ResponseEntity<WorkspaceThreadMessageResponse> getMessage(
            @RequestHeader(name = "Authorization", required = false) String authorization,
            @RequestHeader(name = "X-Memo-Echo-User-Id", defaultValue = "local-user") String userId,
            @PathVariable String threadId,
            @PathVariable String messageId
    ) {
        String resolvedUserId = userContextResolver.resolve(authorization, userId);
        return ResponseEntity.ok(threadService.getMessage(resolvedUserId, threadId, messageId));
    }

    /** 发送一条用户消息并同步执行命令，返回用户消息、Agent 回执与命令响应。 */
    @PostMapping("/{threadId}/messages")
    public ResponseEntity<WorkspaceThreadMessageSendResponse> sendMessage(
            @RequestHeader(name = "Authorization", required = false) String authorization,
            @RequestHeader(name = "X-Memo-Echo-User-Id", defaultValue = "local-user") String userId,
            @PathVariable String threadId,
            @Valid @RequestBody SendThreadMessageRequest request
    ) {
        String resolvedUserId = userContextResolver.resolve(authorization, userId);
        return ResponseEntity.ok(threadService.sendMessage(resolvedUserId, threadId, request.content()));
    }

    public record CreateThreadRequest(@Size(max = 200) String title) {
    }

    public record UpdateThreadRequest(
            @Size(max = 200) String title,
            Boolean pinned,
            Boolean archived
    ) {
    }

    public record SendThreadMessageRequest(
            @NotBlank @Size(max = 8000) String content
    ) {
    }
}