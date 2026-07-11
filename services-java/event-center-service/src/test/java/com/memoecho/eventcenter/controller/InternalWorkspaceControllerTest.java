package com.memoecho.eventcenter.controller;

import com.memoecho.eventcenter.dto.WorkspaceBriefingOverviewResponse;
import com.memoecho.eventcenter.dto.WorkspaceBriefingResponse;
import com.memoecho.eventcenter.dto.WorkspaceConversationDigestResponse;
import com.memoecho.eventcenter.dto.WorkspaceInboxItemResponse;
import com.memoecho.eventcenter.dto.WorkspaceInboxResponse;
import com.memoecho.eventcenter.dto.WorkspaceScheduleDigestResponse;
import com.memoecho.eventcenter.dto.WorkspaceSuggestedActionResponse;
import com.memoecho.eventcenter.dto.WorkspaceTaskDigestResponse;
import com.memoecho.eventcenter.service.WorkspaceBriefingApplicationService;
import com.memoecho.eventcenter.service.WorkspaceInboxApplicationService;
import org.junit.jupiter.api.Test;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.boot.test.autoconfigure.web.servlet.WebMvcTest;
import org.springframework.boot.test.mock.mockito.MockBean;
import org.springframework.test.web.servlet.MockMvc;

import java.time.LocalDateTime;
import java.util.List;

import static org.mockito.BDDMockito.given;
import static org.mockito.Mockito.verify;
import static org.springframework.test.web.servlet.request.MockMvcRequestBuilders.get;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.jsonPath;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.status;

@WebMvcTest(InternalWorkspaceController.class)
class InternalWorkspaceControllerTest {

    @Autowired
    private MockMvc mockMvc;

    @MockBean
    private WorkspaceBriefingApplicationService workspaceBriefingApplicationService;

    @MockBean
    private WorkspaceInboxApplicationService workspaceInboxApplicationService;

    @Test
    void shouldReturnWorkspaceBriefing() throws Exception {
        // 这个测试函数的作用是验证登录摘要接口会把请求参数交给聚合服务并返回前端所需结构。
        WorkspaceBriefingResponse response = new WorkspaceBriefingResponse(
                "2026-07-08T00:10:00Z",
                480,
                new WorkspaceBriefingOverviewResponse(
                        "Hi, freeze，你离开的这 8 小时里，我帮你整理出 2 条重点消息、1 条待办和 1 条今天的日程。",
                        "建议你先从待办“完成项目周报”开始，它已经到期或今天截止。",
                        2,
                        1,
                        1,
                        1
                ),
                List.of(new WorkspaceConversationDigestResponse(
                        "qq",
                        "group",
                        "1098307542",
                        "Memo Echo项目小组",
                        "freeze",
                        "今晚提交项目周报",
                        "2026-07-08T00:00:00Z",
                        "urgent",
                        "这条会话被判定为高优先级，可能包含通知、截止时间或需要即时关注的信息。",
                        "NEEDS_CONFIRMATION",
                        "CONFIRM_REQUIRED",
                        true
                )),
                List.of(new WorkspaceTaskDigestResponse(
                        "task-001",
                        "完成项目周报",
                        "整理本周进展并发群里",
                        "high",
                        "pending",
                        LocalDateTime.of(2026, 7, 8, 18, 0)
                )),
                List.of(new WorkspaceScheduleDigestResponse(
                        "schedule-001",
                        "项目例会",
                        LocalDateTime.of(2026, 7, 8, 14, 0),
                        LocalDateTime.of(2026, 7, 8, 15, 0),
                        "A01-N105",
                        "项目周会"
                )),
                List.of(new WorkspaceSuggestedActionResponse(
                        "task",
                        "优先处理待办任务",
                        "这项任务已经到期或今天截止，建议你先把它完成。",
                        "task-001"
                ))
        );

        given(workspaceBriefingApplicationService.buildBriefing("freeze", "2597164807", 480, 5, 5, 5))
                .willReturn(response);

        mockMvc.perform(get("/internal/workspace/briefing")
                        .param("senderId", "2597164807")
                        .param("userName", "freeze"))
                .andExpect(status().isOk())
                .andExpect(jsonPath("$.overview.importantConversationCount").value(2))
                .andExpect(jsonPath("$.overview.actionRequiredCount").value(1))
                .andExpect(jsonPath("$.overview.pendingTaskCount").value(1))
                .andExpect(jsonPath("$.importantConversations[0].chatName").value("Memo Echo项目小组"))
                .andExpect(jsonPath("$.importantConversations[0].actionRequired").value(true))
                .andExpect(jsonPath("$.pendingTasks[0].title").value("完成项目周报"))
                .andExpect(jsonPath("$.todaySchedules[0].title").value("项目例会"))
                .andExpect(jsonPath("$.suggestedActions[0].type").value("task"));

        verify(workspaceBriefingApplicationService).buildBriefing("freeze", "2597164807", 480, 5, 5, 5);
    }

    @Test
    void shouldReturnWorkspaceInbox() throws Exception {
        // 这个测试函数的作用是验证收件箱接口能够返回 UI 所需的事件、草稿和待处理状态，并正确转发筛选参数。
        WorkspaceInboxResponse response = new WorkspaceInboxResponse(
                "2026-07-10T08:00:00Z",
                "NEW",
                2,
                1,
                1,
                1,
                List.of(new WorkspaceInboxItemResponse(
                        "qq:message:private:1001",
                        "qq",
                        "private",
                        "2597164807",
                        "freeze",
                        "2597164807",
                        "freeze",
                        "下午两点开会",
                        "2026-07-10T06:00:00Z",
                        "social_reply",
                        "NEEDS_CONFIRMATION",
                        "CONFIRM_REQUIRED",
                        "好的，下午两点见。",
                        true,
                        true,
                        "NEW",
                        null,
                        "",
                        null
                ))
        );

        given(workspaceInboxApplicationService.buildInbox("NEW", 20)).willReturn(response);

        mockMvc.perform(get("/internal/workspace/inbox")
                        .param("inboxStatus", "NEW")
                        .param("limit", "20"))
                .andExpect(status().isOk())
                .andExpect(jsonPath("$.totalCount").value(2))
                .andExpect(jsonPath("$.actionRequiredCount").value(1))
                .andExpect(jsonPath("$.items[0].eventId").value("qq:message:private:1001"))
                .andExpect(jsonPath("$.items[0].replyDraft").value("好的，下午两点见。"))
                .andExpect(jsonPath("$.items[0].actionRequired").value(true));

        verify(workspaceInboxApplicationService).buildInbox("NEW", 20);
    }
}
