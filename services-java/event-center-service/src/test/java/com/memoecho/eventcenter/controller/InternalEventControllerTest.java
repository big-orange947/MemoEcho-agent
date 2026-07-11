package com.memoecho.eventcenter.controller;

import com.memoecho.eventcenter.dto.DispatchResult;
import com.memoecho.eventcenter.dto.DraftConfirmRequest;
import com.memoecho.eventcenter.dto.DraftRejectRequest;
import com.memoecho.eventcenter.dto.EventIngestResponse;
import com.memoecho.eventcenter.dto.StoredEventResponse;
import com.memoecho.eventcenter.dto.SnoozeEventRequest;
import com.memoecho.eventcenter.dto.UnifiedEventPayload;
import com.memoecho.eventcenter.service.EventCenterApplicationService;
import org.junit.jupiter.api.Test;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.boot.test.autoconfigure.web.servlet.WebMvcTest;
import org.springframework.boot.test.mock.mockito.MockBean;
import org.springframework.http.MediaType;
import org.springframework.test.web.servlet.MockMvc;

import static org.mockito.ArgumentMatchers.any;
import static org.mockito.ArgumentMatchers.argThat;
import static org.mockito.ArgumentMatchers.eq;
import static org.mockito.BDDMockito.given;
import static org.mockito.Mockito.verify;
import static org.springframework.test.web.servlet.request.MockMvcRequestBuilders.post;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.jsonPath;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.status;

@WebMvcTest(InternalEventController.class)
class InternalEventControllerTest {

    @Autowired
    private MockMvc mockMvc;

    @MockBean
    private EventCenterApplicationService applicationService;

    @Test
    void shouldAcceptUnifiedEvent() throws Exception {
        given(applicationService.ingest(any(UnifiedEventPayload.class)))
                .willReturn(new EventIngestResponse(
                        "qq:message:group:1",
                        true,
                        false,
                        new DispatchResult(true, 200, null, null),
                        "Event accepted by event center."
                ));

        String payload = """
                {
                  "eventId": "qq:message:group:1",
                  "platform": "qq",
                  "eventType": "message",
                  "chatType": "group",
                  "chatId": "138178088",
                  "text": "meeting at 14:00 in A01-N105"
                }
                """;

        mockMvc.perform(post("/internal/events/ingest")
                        .contentType(MediaType.APPLICATION_JSON)
                        .content(payload))
                .andExpect(status().isOk())
                .andExpect(jsonPath("$.accepted").value(true))
                .andExpect(jsonPath("$.duplicate").value(false))
                .andExpect(jsonPath("$.eventId").value("qq:message:group:1"));
    }

    @Test
    void shouldConfirmEditedDraft() throws Exception {
        // 这个测试函数的作用是验证前端可提交编辑后的草稿，并拿到确认发送后的最新事件状态。
        given(applicationService.confirmDraft(eq("qq:message:private:1"), any(DraftConfirmRequest.class)))
                .willReturn(eventResponse("MANUALLY_SENT", "SENT", false, "确认后发送的文本", "CONFIRMED", "DONE"));

        mockMvc.perform(post("/internal/events/qq:message:private:1/draft/confirm")
                        .contentType(MediaType.APPLICATION_JSON)
                        .content("{\"message\":\"确认后发送的文本\",\"note\":\"已检查语气\"}"))
                .andExpect(status().isOk())
                .andExpect(jsonPath("$.processingStatus").value("MANUALLY_SENT"))
                .andExpect(jsonPath("$.writeBackStatus").value("SENT"))
                .andExpect(jsonPath("$.replyDraft").value("确认后发送的文本"))
                .andExpect(jsonPath("$.lastAction").value("CONFIRMED"));

        verify(applicationService).confirmDraft(
                eq("qq:message:private:1"),
                argThat(request -> "确认后发送的文本".equals(request.message()) && "已检查语气".equals(request.note()))
        );
    }

    @Test
    void shouldRejectDraftAndRetryEvent() throws Exception {
        // 这个测试函数的作用是验证拒绝和重试两个工作台动作都映射到独立接口，前端无需复用确认发送接口。
        given(applicationService.rejectDraft(eq("qq:message:private:2"), any(DraftRejectRequest.class)))
                .willReturn(eventResponse("DRAFT_REJECTED", "REJECTED", false, "不发送的草稿", "REJECTED", "DONE"));
        given(applicationService.retryEvent("qq:message:private:3"))
                .willReturn(eventResponse("NEEDS_CONFIRMATION", "CONFIRM_REQUIRED", true, "重试后草稿", "RETRIED", "NEW"));

        mockMvc.perform(post("/internal/events/qq:message:private:2/draft/reject")
                        .contentType(MediaType.APPLICATION_JSON)
                        .content("{\"reason\":\"需要自己回复\"}"))
                .andExpect(status().isOk())
                .andExpect(jsonPath("$.writeBackStatus").value("REJECTED"))
                .andExpect(jsonPath("$.lastAction").value("REJECTED"));

        mockMvc.perform(post("/internal/events/qq:message:private:3/retry"))
                .andExpect(status().isOk())
                .andExpect(jsonPath("$.processingStatus").value("NEEDS_CONFIRMATION"))
                .andExpect(jsonPath("$.lastAction").value("RETRIED"));

        verify(applicationService).rejectDraft(
                eq("qq:message:private:2"),
                argThat(request -> "需要自己回复".equals(request.reason()))
        );
        verify(applicationService).retryEvent("qq:message:private:3");
    }

    @Test
    void shouldUpdateInboxStateAndParseSnoozeTime() throws Exception {
        // 这个测试函数的作用是验证收件箱接口能分别处理已读和稍后处理，并正确解析 ISO-8601 时间字段。
        given(applicationService.markInboxRead("qq:message:private:4"))
                .willReturn(eventResponse("PROCESSED", "NONE", false, "", "RECEIVED", "READ"));
        given(applicationService.snoozeInboxEvent(eq("qq:message:private:5"), any(SnoozeEventRequest.class)))
                .willReturn(eventResponse("PROCESSED", "NONE", false, "", "RECEIVED", "SNOOZED"));

        mockMvc.perform(post("/internal/events/qq:message:private:4/inbox/read"))
                .andExpect(status().isOk())
                .andExpect(jsonPath("$.inboxStatus").value("READ"));

        mockMvc.perform(post("/internal/events/qq:message:private:5/inbox/snooze")
                        .contentType(MediaType.APPLICATION_JSON)
                        .content("{\"snoozedUntil\":\"2026-07-10T13:00:00Z\"}"))
                .andExpect(status().isOk())
                .andExpect(jsonPath("$.inboxStatus").value("SNOOZED"));

        verify(applicationService).markInboxRead("qq:message:private:4");
        verify(applicationService).snoozeInboxEvent(
                eq("qq:message:private:5"),
                argThat(request -> "2026-07-10T13:00:00Z".equals(request.snoozedUntil().toString()))
        );
    }

    private StoredEventResponse eventResponse(
            String processingStatus,
            String writeBackStatus,
            boolean needHumanConfirmation,
            String replyDraft,
            String lastAction,
            String inboxStatus
    ) {
        // 这个测试辅助函数的作用是生成前端动作接口需要的完整事件响应，避免每个测试重复填写无关字段。
        return new StoredEventResponse(
                "qq:message:private:test",
                "qq",
                "message",
                "private",
                "2597164807",
                "你好",
                "2026-07-10T08:00:00Z",
                "2026-07-10T08:00:01Z",
                processingStatus,
                "草稿操作完成。",
                "social_reply",
                writeBackStatus,
                needHumanConfirmation,
                "2026-07-10T08:00:02Z",
                replyDraft,
                null,
                lastAction,
                "测试备注",
                "2026-07-10T08:00:03Z",
                inboxStatus,
                "2026-07-10T08:00:04Z",
                null
        );
    }
}
