package com.memoecho.eventcenter.controller;

import com.memoecho.eventcenter.dto.HistoryTrainingSyncResponse;
import com.memoecho.eventcenter.service.ConversationHistoryTrainingService;
import com.memoecho.eventcenter.service.LocalUserContextResolver;
import org.springframework.http.ResponseEntity;
import org.springframework.web.bind.annotation.PathVariable;
import org.springframework.web.bind.annotation.PostMapping;
import org.springframework.web.bind.annotation.RequestHeader;
import org.springframework.web.bind.annotation.RequestMapping;
import org.springframework.web.bind.annotation.RequestParam;
import org.springframework.web.bind.annotation.RestController;

/** 管理用户显式授权的历史消息训练样本同步。 */
@RestController
@RequestMapping("/internal/conversation-profiles")
public class InternalHistoryTrainingController {

    private final ConversationHistoryTrainingService trainingService;
    private final LocalUserContextResolver userContextResolver;

    public InternalHistoryTrainingController(
            ConversationHistoryTrainingService trainingService,
            LocalUserContextResolver userContextResolver
    ) {
        this.trainingService = trainingService;
        this.userContextResolver = userContextResolver;
    }

    /** 用户保存授权后可调用此接口同步本账号在目标私聊中的历史文本。 */
    @PostMapping("/{profileId}/history-training/sync")
    public ResponseEntity<HistoryTrainingSyncResponse> sync(
            @RequestHeader(name = "Authorization", required = false) String authorization,
            @RequestHeader(name = "X-Memo-Echo-User-Id", defaultValue = "local-user") String userId,
            @PathVariable String profileId,
            @RequestParam(defaultValue = "100") Integer count
    ) {
        String resolvedUserId = userContextResolver.resolve(authorization, userId);
        return ResponseEntity.ok(trainingService.sync(resolvedUserId, profileId, count));
    }

    /** 用户可主动补拉最近历史；保存设定集并开启该选项时也会自动触发同一逻辑。 */
    @PostMapping("/{profileId}/history-context/sync")
    public ResponseEntity<HistoryTrainingSyncResponse> syncContext(
            @RequestHeader(name = "Authorization", required = false) String authorization,
            @RequestHeader(name = "X-Memo-Echo-User-Id", defaultValue = "local-user") String userId,
            @PathVariable String profileId,
            @RequestParam(defaultValue = "100") Integer count
    ) {
        String resolvedUserId = userContextResolver.resolve(authorization, userId);
        return ResponseEntity.ok(trainingService.syncConversationContext(resolvedUserId, profileId, count));
    }
}
