package com.memoecho.eventcenter.controller;

import com.memoecho.eventcenter.dto.QceImportPreviewResponse;
import com.memoecho.eventcenter.dto.QceImportRequest;
import com.memoecho.eventcenter.dto.QceImportResponse;
import com.memoecho.eventcenter.service.LocalUserContextResolver;
import com.memoecho.eventcenter.service.QceHistoryImportService;
import org.springframework.http.ResponseEntity;
import org.springframework.web.bind.annotation.PostMapping;
import org.springframework.web.bind.annotation.RequestBody;
import org.springframework.web.bind.annotation.RequestHeader;
import org.springframework.web.bind.annotation.RequestMapping;
import org.springframework.web.bind.annotation.RestController;

/**
 * QCE 历史导入接口。
 *
 * <p>导入必须经过预览与用户确认；接口只接收客户端读取后的 JSON，绝不主动读取 QQ 或用户磁盘。</p>
 */
@RestController
@RequestMapping("/internal/history-imports/qce")
public class InternalQceHistoryImportController {

    private final QceHistoryImportService importService;
    private final LocalUserContextResolver userContextResolver;

    public InternalQceHistoryImportController(
            QceHistoryImportService importService,
            LocalUserContextResolver userContextResolver
    ) {
        this.importService = importService;
        this.userContextResolver = userContextResolver;
    }

    /**
     * 解析文件并给出消息和附件预览，不会产生任何写入。
     */
    @PostMapping("/preview")
    public ResponseEntity<QceImportPreviewResponse> preview(
            @RequestBody QceImportRequest request,
            @RequestHeader(name = "Authorization", required = false) String authorization,
            @RequestHeader(name = "X-Memo-Echo-User-Id", defaultValue = "local-user") String userId
    ) {
        // 预览虽然不写库，也必须经过登录校验，避免本机其他页面滥用解析接口处理私密导出文件。
        userContextResolver.resolve(authorization, userId);
        return ResponseEntity.ok(importService.preview(request));
    }

    /**
     * 用户确认后写入历史事件；导入记录固定跳过 Runtime 派发。
     */
    @PostMapping
    public ResponseEntity<QceImportResponse> importHistory(
            @RequestBody QceImportRequest request,
            @RequestHeader(name = "Authorization", required = false) String authorization,
            @RequestHeader(name = "X-Memo-Echo-User-Id", defaultValue = "local-user") String userId
    ) {
        String resolvedUserId = userContextResolver.resolve(authorization, userId);
        return ResponseEntity.ok(importService.importHistory(resolvedUserId, request));
    }
}
