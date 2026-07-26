package com.memoecho.eventcenter.controller;

import com.memoecho.eventcenter.dto.ConversationProgressResponse;
import com.memoecho.eventcenter.service.ConversationProgressApplicationService;
import com.memoecho.eventcenter.service.LocalUserContextResolver;
import org.springframework.http.ResponseEntity;
import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.PathVariable;
import org.springframework.web.bind.annotation.RequestHeader;
import org.springframework.web.bind.annotation.RequestMapping;
import org.springframework.web.bind.annotation.RequestParam;
import org.springframework.web.bind.annotation.RestController;

@RestController
@RequestMapping("/internal/workspace/conversations")
public class InternalConversationProgressController {

    private final ConversationProgressApplicationService conversationProgressApplicationService;
    private final LocalUserContextResolver userContextResolver;

    /** 注入进度快照服务和当前本地用户解析器。 */
    public InternalConversationProgressController(
            ConversationProgressApplicationService conversationProgressApplicationService,
            LocalUserContextResolver userContextResolver
    ) {
        this.conversationProgressApplicationService = conversationProgressApplicationService;
        this.userContextResolver = userContextResolver;
    }

    /**
     * 用户点击“查看上下文”后才执行该接口，返回当时的消息时间线和自然语言进度。
     */
    @GetMapping("/{chatId}/progress")
    public ResponseEntity<ConversationProgressResponse> progress(
            @PathVariable String chatId,
            @RequestParam(required = false) String platform,
            @RequestParam(required = false) String chatType,
            @RequestParam(required = false, defaultValue = "60") Integer limit,
            @RequestParam(required = false) String lastSeenAgentEventId,
            @RequestHeader(name = "Authorization", required = false) String authorization,
            @RequestHeader(name = "X-Memo-Echo-User-Id", defaultValue = "local-user") String userId
    ) {
        String ownerUserId = userContextResolver.resolve(authorization, userId);
        return ResponseEntity.ok(conversationProgressApplicationService.buildSnapshot(
                ownerUserId,
                platform,
                chatType,
                chatId,
                limit,
                lastSeenAgentEventId
        ));
    }
}
