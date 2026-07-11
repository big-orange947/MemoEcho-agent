package com.memoecho.eventcenter.controller;

import com.memoecho.eventcenter.dto.ConversationMessageResponse;
import com.memoecho.eventcenter.dto.ConversationOverviewResponse;
import com.memoecho.eventcenter.dto.ConversationSummaryResponse;
import com.memoecho.eventcenter.service.EventCenterApplicationService;
import org.junit.jupiter.api.Test;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.boot.test.autoconfigure.web.servlet.WebMvcTest;
import org.springframework.boot.test.mock.mockito.MockBean;
import org.springframework.test.web.servlet.MockMvc;

import java.util.List;

import static org.mockito.BDDMockito.given;
import static org.mockito.Mockito.verify;
import static org.springframework.test.web.servlet.request.MockMvcRequestBuilders.get;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.jsonPath;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.status;

@WebMvcTest(InternalConversationController.class)
class InternalConversationControllerTest {

    @Autowired
    private MockMvc mockMvc;

    @MockBean
    private EventCenterApplicationService applicationService;

    @Test
    void shouldReturnConversationOverview() throws Exception {
        given(applicationService.getConversationOverview())
                .willReturn(new ConversationOverviewResponse(8, 2, 6, 3, 6, 4));

        mockMvc.perform(get("/internal/conversations/overview"))
                .andExpect(status().isOk())
                .andExpect(jsonPath("$.totalConversations").value(8))
                .andExpect(jsonPath("$.urgentConversations").value(3))
                .andExpect(jsonPath("$.activeInLastHourConversations").value(4));

        verify(applicationService).getConversationOverview();
    }

    @Test
    void shouldListConversationSummaries() throws Exception {
        given(applicationService.findConversationSummaries("qq", "group", "schedule", "urgent", 60))
                .willReturn(List.of(new ConversationSummaryResponse(
                        "qq",
                        "group",
                        "1098307542",
                        "memo-echo-group",
                        "freeze",
                        "schedule for today",
                        "2026-07-07T07:43:00Z",
                        "schedule_extract",
                        "urgent",
                        "NEEDS_CONFIRMATION",
                        "CONFIRM_REQUIRED",
                        true,
                        0,
                        1,
                        true,
                        true
                )));

        mockMvc.perform(get("/internal/conversations")
                        .param("platform", "qq")
                        .param("chatType", "group")
                        .param("keyword", "schedule")
                        .param("dispatchMode", "urgent")
                        .param("activeWithinMinutes", "60"))
                .andExpect(status().isOk())
                .andExpect(jsonPath("$[0].platform").value("qq"))
                .andExpect(jsonPath("$[0].chatType").value("group"))
                .andExpect(jsonPath("$[0].chatId").value("1098307542"))
                .andExpect(jsonPath("$[0].lastSenderName").value("freeze"))
                .andExpect(jsonPath("$[0].lastRoute").value("schedule_extract"))
                .andExpect(jsonPath("$[0].lastDispatchMode").value("urgent"));

        verify(applicationService).findConversationSummaries("qq", "group", "schedule", "urgent", 60);
    }

    @Test
    void shouldListConversationMessages() throws Exception {
        given(applicationService.findConversationMessages("1098307542", "qq", "group", 20))
                .willReturn(List.of(new ConversationMessageResponse(
                        "qq:message:group:1",
                        "qq",
                        "group",
                        "1098307542",
                        "memo-echo-group",
                        "2597164807",
                        "freeze",
                        "owner",
                        "@assistant meeting at 14:00 in A01-N105",
                        "2026-07-07T07:43:00Z",
                        List.of("3969785168"),
                        List.of(),
                        false,
                        false,
                        "schedule_extract",
                        "urgent",
                        "NEEDS_CONFIRMATION",
                        "日程已经提取，等待用户确认。",
                        "CONFIRM_REQUIRED",
                        true,
                        "你好，会议日程已经为你整理好了。",
                        "NEW",
                        null
                )));

        mockMvc.perform(get("/internal/conversations/1098307542/messages")
                        .param("platform", "qq")
                        .param("chatType", "group")
                        .param("limit", "20"))
                .andExpect(status().isOk())
                .andExpect(jsonPath("$[0].eventId").value("qq:message:group:1"))
                .andExpect(jsonPath("$[0].senderName").value("freeze"))
                .andExpect(jsonPath("$[0].route").value("schedule_extract"))
                .andExpect(jsonPath("$[0].dispatchMode").value("urgent"))
                .andExpect(jsonPath("$[0].processingStatus").value("NEEDS_CONFIRMATION"))
                .andExpect(jsonPath("$[0].needHumanConfirmation").value(true))
                .andExpect(jsonPath("$[0].replyDraft").value("你好，会议日程已经为你整理好了。"))
                .andExpect(jsonPath("$[0].inboxStatus").value("NEW"));

        verify(applicationService).findConversationMessages("1098307542", "qq", "group", 20);
    }
}
