package com.memoecho.eventcenter.service;

import com.memoecho.eventcenter.dto.ConversationSummaryResponse;
import com.memoecho.eventcenter.dto.ScheduleServiceScheduleResponse;
import com.memoecho.eventcenter.dto.TaskServiceTaskResponse;
import com.memoecho.eventcenter.dto.WorkspaceBriefingResponse;
import org.junit.jupiter.api.Test;

import java.time.LocalDate;
import java.time.LocalDateTime;
import java.util.List;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertTrue;
import static org.mockito.BDDMockito.given;
import static org.mockito.Mockito.mock;

class WorkspaceBriefingApplicationServiceTest {

    @Test
    void shouldBuildBriefingFromConversationsTasksAndSchedules() {
        // 这个测试函数的作用是验证聚合服务会把重点消息、待办、今日日程和建议动作组装成统一摘要包。
        EventCenterApplicationService eventCenterApplicationService = mock(EventCenterApplicationService.class);
        TaskServiceQueryClient taskServiceQueryClient = mock(TaskServiceQueryClient.class);
        ScheduleServiceQueryClient scheduleServiceQueryClient = mock(ScheduleServiceQueryClient.class);

        WorkspaceBriefingApplicationService service = new WorkspaceBriefingApplicationService(
                eventCenterApplicationService,
                taskServiceQueryClient,
                scheduleServiceQueryClient
        );

        LocalDate today = LocalDate.now();
        List<ConversationSummaryResponse> conversations = List.of(
                new ConversationSummaryResponse(
                        "qq",
                        "group",
                        "1098307542",
                        "Memo Echo项目小组",
                        "freeze",
                        "今晚提交项目周报",
                        "2026-07-08T00:00:00Z",
                        "task_plan",
                        "urgent",
                        "NEEDS_CONFIRMATION",
                        "CONFIRM_REQUIRED",
                        true,
                        0,
                        1,
                        true,
                        true
                ),
                new ConversationSummaryResponse(
                        "qq",
                        "private",
                        "2597164807",
                        "freeze",
                        "alice",
                        "记得看一下会议通知",
                        "2026-07-07T23:30:00Z",
                        "social_reply",
                        "normal",
                        "AUTO_REPLIED",
                        "SENT",
                        false,
                        0,
                        0,
                        true,
                        true
                )
        );
        List<TaskServiceTaskResponse> tasks = List.of(
                new TaskServiceTaskResponse(
                        "task-001",
                        "source-task-001",
                        "qq",
                        "1098307542",
                        "2597164807",
                        "完成项目周报",
                        "整理本周进展并发群里",
                        today.atTime(18, 0),
                        "high",
                        "pending",
                        "high",
                        today.atTime(9, 0)
                )
        );
        List<ScheduleServiceScheduleResponse> schedules = List.of(
                new ScheduleServiceScheduleResponse(
                        "schedule-001",
                        "source-schedule-001",
                        "qq",
                        "1098307542",
                        "2597164807",
                        "项目例会",
                        today.atTime(14, 0),
                        today.atTime(15, 0),
                        "A01-N105",
                        "项目周会",
                        "项目组成员",
                        "high",
                        today.atTime(8, 0)
                ),
                new ScheduleServiceScheduleResponse(
                        "schedule-002",
                        "source-schedule-002",
                        "qq",
                        "1098307542",
                        "88880001",
                        "明天答辩彩排",
                        today.plusDays(1).atTime(10, 0),
                        today.plusDays(1).atTime(11, 0),
                        "A02-201",
                        "答辩彩排",
                        "项目组成员",
                        "medium",
                        today.atTime(8, 30)
                )
        );

        given(eventCenterApplicationService.findConversationSummaries(null, null, null, null, 480))
                .willReturn(conversations);
        given(taskServiceQueryClient.listPendingTasks("2597164807", 5))
                .willReturn(tasks);
        // 第二条日程来自另一位群成员；工作台仍应把它作为当前本地用户的近期安排展示出来。
        given(scheduleServiceQueryClient.listWorkspaceSchedules())
                .willReturn(schedules);

        WorkspaceBriefingResponse response = service.buildBriefing("freeze", "2597164807", 480, 5, 5, 5);

        assertEquals(2, response.importantConversations().size());
        assertEquals(1, response.pendingTasks().size());
        assertEquals(1, response.todaySchedules().size());
        assertEquals(2, response.upcomingSchedules().size());
        assertEquals(4, response.suggestedActions().size());
        assertEquals(1, response.overview().actionRequiredCount());
        assertTrue(response.importantConversations().get(0).actionRequired());
        assertTrue(response.overview().openingLine().contains("freeze"));
        assertTrue(response.overview().suggestedStart().contains("完成项目周报"));
    }
}
