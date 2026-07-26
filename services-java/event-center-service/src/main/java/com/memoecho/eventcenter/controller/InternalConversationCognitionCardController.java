package com.memoecho.eventcenter.controller;

import com.memoecho.eventcenter.dto.ConversationCognitionCardResponse;
import com.memoecho.eventcenter.dto.ConversationCognitionCardUpsertRequest;
import com.memoecho.eventcenter.service.ConversationCognitionCardApplicationService;
import com.memoecho.eventcenter.service.ConversationCognitionRefreshService;
import com.memoecho.eventcenter.service.LocalUserContextResolver;
import jakarta.validation.Valid;
import org.springframework.http.ResponseEntity;
import org.springframework.web.bind.annotation.DeleteMapping;
import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.PostMapping;
import org.springframework.web.bind.annotation.PutMapping;
import org.springframework.web.bind.annotation.RequestBody;
import org.springframework.web.bind.annotation.RequestHeader;
import org.springframework.web.bind.annotation.RequestMapping;
import org.springframework.web.bind.annotation.RequestParam;
import org.springframework.web.bind.annotation.RestController;

/** 提供桌面端和 Agent Runtime 使用的会话认知卡接口。 */
@RestController
@RequestMapping("/internal/conversation-cognition")
public class InternalConversationCognitionCardController {

    private final ConversationCognitionCardApplicationService applicationService;
    private final ConversationCognitionRefreshService refreshService;
    private final LocalUserContextResolver userContextResolver;

    /** 注入认知卡服务和用户解析器，所有入口都在控制器建立所有权边界。 */
    public InternalConversationCognitionCardController(
            ConversationCognitionCardApplicationService applicationService,
            ConversationCognitionRefreshService refreshService,
            LocalUserContextResolver userContextResolver
    ) {
        this.applicationService = applicationService;
        this.refreshService = refreshService;
        this.userContextResolver = userContextResolver;
    }

    /** 用户打开认知卡或主动点击刷新时，按最新消息增量生成认知结果。 */
    @PostMapping("/refresh")
    public ResponseEntity<ConversationCognitionCardResponse> refresh(
            @RequestParam String platform,
            @RequestParam String chatType,
            @RequestParam String chatId,
            @RequestParam(defaultValue = "80") Integer limit,
            @RequestHeader(name = "Authorization", required = false) String authorization,
            @RequestHeader(name = "X-Memo-Echo-User-Id", defaultValue = "local-user") String userId
    ) {
        return ResponseEntity.ok(refreshService.refresh(
                userContextResolver.resolve(authorization, userId), platform, chatType, chatId, limit));
    }

    /** 桌面端按会话读取当前认知卡。 */
    @GetMapping
    public ResponseEntity<ConversationCognitionCardResponse> get(
            @RequestParam String platform,
            @RequestParam String chatType,
            @RequestParam String chatId,
            @RequestHeader(name = "Authorization", required = false) String authorization,
            @RequestHeader(name = "X-Memo-Echo-User-Id", defaultValue = "local-user") String userId
    ) {
        return ResponseEntity.ok(applicationService.get(
                userContextResolver.resolve(authorization, userId), platform, chatType, chatId));
    }

    /** 桌面端保存用户修正，服务端会强制把显式字段标记为用户覆盖。 */
    @PutMapping
    public ResponseEntity<ConversationCognitionCardResponse> upsertByUser(
            @Valid @RequestBody ConversationCognitionCardUpsertRequest request,
            @RequestHeader(name = "Authorization", required = false) String authorization,
            @RequestHeader(name = "X-Memo-Echo-User-Id", defaultValue = "local-user") String userId
    ) {
        return ResponseEntity.ok(applicationService.upsertByUser(
                userContextResolver.resolve(authorization, userId), request));
    }

    /** Runtime 提交最新推断；必须携带有效 Runtime Token，且不能覆盖用户锁定字段。 */
    @PutMapping("/runtime")
    public ResponseEntity<ConversationCognitionCardResponse> upsertInference(
            @Valid @RequestBody ConversationCognitionCardUpsertRequest request,
            @RequestHeader(name = "X-Memo-Echo-Runtime-Token") String runtimeToken,
            @RequestHeader(name = "X-Memo-Echo-User-Id", defaultValue = "local-user") String userId
    ) {
        return ResponseEntity.ok(applicationService.upsertInference(
                userContextResolver.resolveRuntimeUser(runtimeToken, userId), request));
    }

    /** 用户确认整张认知卡，确认后的已有字段不再被模型刷新。 */
    @PostMapping("/confirm")
    public ResponseEntity<ConversationCognitionCardResponse> confirm(
            @RequestParam String platform,
            @RequestParam String chatType,
            @RequestParam String chatId,
            @RequestHeader(name = "Authorization", required = false) String authorization,
            @RequestHeader(name = "X-Memo-Echo-User-Id", defaultValue = "local-user") String userId
    ) {
        return ResponseEntity.ok(applicationService.confirm(
                userContextResolver.resolve(authorization, userId), platform, chatType, chatId));
    }

    /** 删除当前用户指定会话的认知卡。 */
    @DeleteMapping
    public ResponseEntity<Void> delete(
            @RequestParam String platform,
            @RequestParam String chatType,
            @RequestParam String chatId,
            @RequestHeader(name = "Authorization", required = false) String authorization,
            @RequestHeader(name = "X-Memo-Echo-User-Id", defaultValue = "local-user") String userId
    ) {
        applicationService.delete(userContextResolver.resolve(authorization, userId), platform, chatType, chatId);
        return ResponseEntity.noContent().build();
    }
}
