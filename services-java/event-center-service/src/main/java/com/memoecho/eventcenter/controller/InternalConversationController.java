package com.memoecho.eventcenter.controller;

import com.memoecho.eventcenter.dto.ConversationMessageResponse;
import com.memoecho.eventcenter.dto.ConversationOverviewResponse;
import com.memoecho.eventcenter.dto.ConversationSummaryResponse;
import com.memoecho.eventcenter.service.EventCenterApplicationService;
import com.memoecho.eventcenter.service.LocalUserContextResolver;
import org.springframework.beans.factory.ObjectProvider;
import org.springframework.http.ResponseEntity;
import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.PathVariable;
import org.springframework.web.bind.annotation.RequestMapping;
import org.springframework.web.bind.annotation.RequestHeader;
import org.springframework.web.bind.annotation.RequestParam;
import org.springframework.web.bind.annotation.RestController;

import java.util.List;

@RestController
@RequestMapping("/internal/conversations")
public class InternalConversationController {

    private final EventCenterApplicationService applicationService;
    private final ObjectProvider<LocalUserContextResolver> userContextResolverProvider;

    public InternalConversationController(
            EventCenterApplicationService applicationService,
            ObjectProvider<LocalUserContextResolver> userContextResolverProvider
    ) {
        this.applicationService = applicationService;
        this.userContextResolverProvider = userContextResolverProvider;
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
            @RequestParam(defaultValue = "50") Integer limit,
            @RequestParam(required = false) String before,
            @RequestParam(required = false) String after,
            @RequestHeader(name = "Authorization", required = false) String authorization,
            @RequestHeader(name = "X-Memo-Echo-User-Id", defaultValue = "default") String userId,
            @RequestHeader(name = "X-Memo-Echo-Runtime-Token", required = false) String runtimeToken
    ) {
        // 会话详情接口按 chatId 取消息，并允许额外带平台和类型做消歧。
        LocalUserContextResolver userContextResolver = userContextResolverProvider.getIfAvailable();
        if (userContextResolver == null) {
            // WebMvcTest 等最小上下文不加载认证服务时，继续兼容旧的会话查询行为。
            return ResponseEntity.ok(applicationService.findConversationMessages(
                    chatId, platform, chatType, limit, before, after));
        }
        String resolvedUserId = authorization != null && !authorization.isBlank()
                ? userContextResolver.resolve(authorization, userId)
                : userContextResolver.resolveRuntimeUser(runtimeToken, userId);
        // 历史读取失败必须如实暴露：服务层已记录请求范围、数据库命中数、过滤前后数量等完整诊断，
        // 调用方（Runtime）据此改用 L0 当前事件继续推理，而不是静默拿到空历史。
        return ResponseEntity.ok(applicationService.findConversationMessages(
                resolvedUserId, chatId, platform, chatType, limit, before, after));
    }
}
