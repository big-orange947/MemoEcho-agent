package com.memoecho.eventcenter.controller;

import com.memoecho.eventcenter.dto.ConversationMessageResponse;
import com.memoecho.eventcenter.dto.ConversationOverviewResponse;
import com.memoecho.eventcenter.dto.ConversationSummaryResponse;
import com.memoecho.eventcenter.service.EventCenterApplicationService;
import org.springframework.http.ResponseEntity;
import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.PathVariable;
import org.springframework.web.bind.annotation.RequestMapping;
import org.springframework.web.bind.annotation.RequestParam;
import org.springframework.web.bind.annotation.RestController;

import java.util.List;

@RestController
@RequestMapping("/internal/conversations")
public class InternalConversationController {

    private final EventCenterApplicationService applicationService;

    public InternalConversationController(EventCenterApplicationService applicationService) {
        this.applicationService = applicationService;
    }

    @GetMapping("/overview")
    public ResponseEntity<ConversationOverviewResponse> overview() {
        // 给前端顶部统计卡片或调试页提供聚合概览数据。
        return ResponseEntity.ok(applicationService.getConversationOverview());
    }

    @GetMapping
    public ResponseEntity<List<ConversationSummaryResponse>> listConversations(
            @RequestParam(required = false) String platform,
            @RequestParam(required = false) String chatType,
            @RequestParam(required = false) String keyword,
            @RequestParam(required = false) String dispatchMode,
            @RequestParam(required = false) Integer activeWithinMinutes
    ) {
        // 会话列表接口支持按平台、类型、关键词、派发模式和活跃时间筛选。
        return ResponseEntity.ok(applicationService.findConversationSummaries(
                platform,
                chatType,
                keyword,
                dispatchMode,
                activeWithinMinutes
        ));
    }

    @GetMapping("/{chatId}/messages")
    public ResponseEntity<List<ConversationMessageResponse>> listConversationMessages(
            @PathVariable String chatId,
            @RequestParam(required = false) String platform,
            @RequestParam(required = false) String chatType,
            @RequestParam(defaultValue = "50") Integer limit
    ) {
        // 会话详情接口按 chatId 取消息，并允许额外带平台和类型做消歧。
        return ResponseEntity.ok(applicationService.findConversationMessages(chatId, platform, chatType, limit));
    }
}
