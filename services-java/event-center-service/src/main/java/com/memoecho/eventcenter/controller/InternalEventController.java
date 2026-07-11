package com.memoecho.eventcenter.controller;

import com.memoecho.eventcenter.dto.EventIngestResponse;
import com.memoecho.eventcenter.dto.ConversationDigestRequest;
import com.memoecho.eventcenter.dto.DraftConfirmRequest;
import com.memoecho.eventcenter.dto.DraftRejectRequest;
import com.memoecho.eventcenter.dto.StoredEventResponse;
import com.memoecho.eventcenter.dto.SnoozeEventRequest;
import com.memoecho.eventcenter.dto.UnifiedEventPayload;
import com.memoecho.eventcenter.service.EventCenterApplicationService;
import jakarta.validation.Valid;
import org.springframework.http.HttpStatus;
import org.springframework.http.ResponseEntity;
import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.PathVariable;
import org.springframework.web.bind.annotation.PostMapping;
import org.springframework.web.bind.annotation.RequestBody;
import org.springframework.web.bind.annotation.RequestMapping;
import org.springframework.web.bind.annotation.RequestParam;
import org.springframework.web.bind.annotation.RestController;

import java.util.List;

@RestController
@RequestMapping("/internal/events")
public class InternalEventController {

    private final EventCenterApplicationService applicationService;

    public InternalEventController(EventCenterApplicationService applicationService) {
        this.applicationService = applicationService;
    }

    @PostMapping("/ingest")
    public ResponseEntity<EventIngestResponse> ingest(@Valid @RequestBody UnifiedEventPayload payload) {
        return ResponseEntity.ok(applicationService.ingest(payload));
    }

    @PostMapping("/digests")
    public ResponseEntity<StoredEventResponse> recordDigest(@Valid @RequestBody ConversationDigestRequest request) {
        // 这个接口的作用是接收 Runtime 后台定时器生成的会话摘要，并直接持久化为工作台事件而不再次派发 Agent。
        return ResponseEntity.ok(applicationService.recordConversationDigest(request));
    }

    @GetMapping
    public ResponseEntity<List<StoredEventResponse>> list(@RequestParam(required = false) String inboxStatus) {
        return ResponseEntity.ok(applicationService.findAll(inboxStatus));
    }

    @GetMapping("/{eventId}")
    public ResponseEntity<StoredEventResponse> getByEventId(@PathVariable String eventId) {
        return applicationService.findByEventId(eventId)
                .map(ResponseEntity::ok)
                .orElseGet(() -> ResponseEntity.status(HttpStatus.NOT_FOUND).build());
    }

    @PostMapping("/{eventId}/draft/confirm")
    public ResponseEntity<StoredEventResponse> confirmDraft(
            @PathVariable String eventId,
            @RequestBody(required = false) DraftConfirmRequest request
    ) {
        // 这个接口的作用是让工作台确认发送草稿；message 可选，传入时表示先编辑再发送。
        return ResponseEntity.ok(applicationService.confirmDraft(eventId, request));
    }

    @PostMapping("/{eventId}/draft/reject")
    public ResponseEntity<StoredEventResponse> rejectDraft(
            @PathVariable String eventId,
            @RequestBody(required = false) DraftRejectRequest request
    ) {
        // 这个接口的作用是让工作台拒绝草稿并记录拒绝原因，不会向外部聊天平台发送消息。
        return ResponseEntity.ok(applicationService.rejectDraft(eventId, request));
    }

    @PostMapping("/{eventId}/retry")
    public ResponseEntity<StoredEventResponse> retry(@PathVariable String eventId) {
        // 这个接口的作用是让工作台对失败事件重新执行 Runtime，获取新的草稿或处理结果。
        return ResponseEntity.ok(applicationService.retryEvent(eventId));
    }

    @PostMapping("/{eventId}/inbox/read")
    public ResponseEntity<StoredEventResponse> markRead(@PathVariable String eventId) {
        // 这个接口的作用是把消息标记为已读；消息仍保留在收件箱中，便于用户之后继续处理。
        return ResponseEntity.ok(applicationService.markInboxRead(eventId));
    }

    @PostMapping("/{eventId}/inbox/done")
    public ResponseEntity<StoredEventResponse> markDone(@PathVariable String eventId) {
        // 这个接口的作用是把消息标记为已处理，后续工作台摘要不会再把它作为待办事项推荐。
        return ResponseEntity.ok(applicationService.markInboxDone(eventId));
    }

    @PostMapping("/{eventId}/inbox/ignore")
    public ResponseEntity<StoredEventResponse> ignore(@PathVariable String eventId) {
        // 这个接口的作用是忽略无关消息，避免它继续出现在重点会话和工作台建议中。
        return ResponseEntity.ok(applicationService.ignoreInboxEvent(eventId));
    }

    @PostMapping("/{eventId}/inbox/snooze")
    public ResponseEntity<StoredEventResponse> snooze(
            @PathVariable String eventId,
            @RequestBody SnoozeEventRequest request
    ) {
        // 这个接口的作用是把消息延后到指定时间再回到工作台，适合“稍后处理”的场景。
        return ResponseEntity.ok(applicationService.snoozeInboxEvent(eventId, request));
    }
}
