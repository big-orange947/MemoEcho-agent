package com.memoecho.eventcenter.controller;

import com.memoecho.eventcenter.dto.ScheduleServiceScheduleResponse;
import com.memoecho.eventcenter.service.LocalUserContextResolver;
import com.memoecho.eventcenter.service.WorkspaceScheduleApplicationService;
import org.junit.jupiter.api.Test;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.boot.test.autoconfigure.web.servlet.WebMvcTest;
import org.springframework.boot.test.mock.mockito.MockBean;
import org.springframework.http.MediaType;
import org.springframework.test.web.servlet.MockMvc;

import java.time.LocalDateTime;

import static org.mockito.ArgumentMatchers.any;
import static org.mockito.BDDMockito.given;
import static org.mockito.Mockito.verify;
import static org.springframework.test.web.servlet.request.MockMvcRequestBuilders.delete;
import static org.springframework.test.web.servlet.request.MockMvcRequestBuilders.post;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.jsonPath;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.status;

@WebMvcTest(InternalWorkspaceScheduleController.class)
class InternalWorkspaceScheduleControllerTest {

    @Autowired
    private MockMvc mockMvc;

    @MockBean
    private WorkspaceScheduleApplicationService scheduleApplicationService;

    @MockBean
    private LocalUserContextResolver userContextResolver;

    @Test
    void shouldCreateManualScheduleForAuthenticatedUser() throws Exception {
        // 这个测试验证 Controller 会使用解析后的可信用户，而不是直接相信请求头里的用户 ID。
        LocalDateTime startTime = LocalDateTime.of(2026, 7, 18, 14, 0);
        given(userContextResolver.resolve("Bearer token", "untrusted-user")).willReturn("user-001");
        given(scheduleApplicationService.createManualSchedule(
                org.mockito.ArgumentMatchers.eq("user-001"), any()
        )).willReturn(new ScheduleServiceScheduleResponse(
                "schedule-1", "manual:user-001:uuid", "local", "manual", "user-001", "项目例会",
                startTime, null, "A01-N105", "讨论进度", null, "manual", LocalDateTime.now()
        ));

        mockMvc.perform(post("/internal/workspace/schedules")
                        .header("Authorization", "Bearer token")
                        .header("X-Memo-Echo-User-Id", "untrusted-user")
                        .contentType(MediaType.APPLICATION_JSON)
                        .content("""
                                {
                                  "title": "项目例会",
                                  "startTime": "2026-07-18 14:00:00",
                                  "location": "A01-N105",
                                  "content": "讨论进度"
                                }
                                """))
                .andExpect(status().isOk())
                .andExpect(jsonPath("$.id").value("schedule-1"))
                .andExpect(jsonPath("$.sourceEventId").value("manual:user-001:uuid"));
    }

    @Test
    void shouldDeleteScheduleForAuthenticatedUser() throws Exception {
        // 这个测试验证删除请求会经过用户解析器和业务层归属检查。
        given(userContextResolver.resolve(null, "local-user")).willReturn("user-001");

        mockMvc.perform(delete("/internal/workspace/schedules/{id}", "schedule-1"))
                .andExpect(status().isNoContent());

        verify(scheduleApplicationService).deleteSchedule("user-001", "schedule-1");
    }
}
