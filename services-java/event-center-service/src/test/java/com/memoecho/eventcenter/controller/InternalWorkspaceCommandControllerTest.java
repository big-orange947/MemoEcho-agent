package com.memoecho.eventcenter.controller;

import com.memoecho.eventcenter.dto.DelegatedTaskResponse;
import com.memoecho.eventcenter.dto.WorkspaceCommandResponse;
import com.memoecho.eventcenter.service.LocalUserContextResolver;
import com.memoecho.eventcenter.service.DelegatedTaskApplicationService;
import com.memoecho.eventcenter.service.DelegatedWorkflowApplicationService;
import com.memoecho.eventcenter.service.WorkspaceCommandApplicationService;
import org.junit.jupiter.api.Test;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.boot.test.autoconfigure.web.servlet.WebMvcTest;
import org.springframework.boot.test.mock.mockito.MockBean;
import org.springframework.http.MediaType;
import org.springframework.test.web.servlet.MockMvc;

import java.util.List;

import static org.mockito.ArgumentMatchers.any;
import static org.mockito.BDDMockito.given;
import static org.mockito.Mockito.verify;
import static org.springframework.test.web.servlet.request.MockMvcRequestBuilders.post;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.jsonPath;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.status;

@WebMvcTest(InternalWorkspaceCommandController.class)
class InternalWorkspaceCommandControllerTest {

    @Autowired
    private MockMvc mockMvc;

    @MockBean
    private WorkspaceCommandApplicationService applicationService;

    @MockBean
    private DelegatedTaskApplicationService delegatedTaskApplicationService;

    @MockBean
    private DelegatedWorkflowApplicationService delegatedWorkflowApplicationService;

    @MockBean
    private LocalUserContextResolver userContextResolver;

    @Test
    void shouldExecuteCommandForAuthenticatedUser() throws Exception {
        // 这个测试函数的作用是验证 Controller 使用 JWT 解析出的用户执行命令并返回结构化结果。
        given(userContextResolver.resolve("Bearer valid-token", "local-user")).willReturn("user-001");
        given(applicationService.execute(any(), any())).willReturn(new WorkspaceCommandResponse(
                "desktop:command:1",
                "success",
                "task_plan",
                "Plan executed",
                "今天先完成项目周报。",
                false,
                List.of(),
                null,
                ""
        ));

        mockMvc.perform(post("/internal/workspace/commands")
                        .header("Authorization", "Bearer valid-token")
                        .contentType(MediaType.APPLICATION_JSON)
                        .content("""
                                {"prompt":"今天应该先做什么？","requestedRoute":"task_plan"}
                                """))
                .andExpect(status().isOk())
                .andExpect(jsonPath("$.status").value("success"))
                .andExpect(jsonPath("$.route").value("task_plan"))
                .andExpect(jsonPath("$.finalReply").value("今天先完成项目周报。"));

        verify(applicationService).execute(any(), any());
    }

    @Test
    void shouldRejectBlankPromptBeforeCallingService() throws Exception {
        // 这个测试函数的作用是验证空命令会在 API 边界被参数校验直接拒绝。
        mockMvc.perform(post("/internal/workspace/commands")
                        .header("Authorization", "Bearer valid-token")
                        .contentType(MediaType.APPLICATION_JSON)
                        .content("""
                                {"prompt":" ","requestedRoute":"task_plan"}
                                """))
                .andExpect(status().isBadRequest());
    }

    @Test
    void shouldPauseDelegatedTaskForAuthenticatedUser() throws Exception {
        // 这个测试函数的作用是验证桌面端暂停操作会绑定当前用户，并返回暂停后的任务状态。
        given(userContextResolver.resolve("Bearer valid-token", "local-user")).willReturn("user-001");
        given(delegatedTaskApplicationService.pause("user-001", "task-001"))
                .willReturn(delegatedTaskResponse("PAUSED"));

        mockMvc.perform(post("/internal/workspace/commands/delegated/task-001/pause")
                        .header("Authorization", "Bearer valid-token"))
                .andExpect(status().isOk())
                .andExpect(jsonPath("$.id").value("task-001"))
                .andExpect(jsonPath("$.status").value("PAUSED"));

        verify(delegatedTaskApplicationService).pause("user-001", "task-001");
    }

    /** 这个辅助函数的作用是构造控制器测试所需的完整委托任务响应。 */
    private DelegatedTaskResponse delegatedTaskResponse(String status) {
        return new DelegatedTaskResponse(
                "task-001", "CONVERSATION_GOAL", status, "帮我联系小号", "小号",
                "qq", "private", "10002", "小号", "确认明天下午是否有空",
                "对方明确接受或拒绝", "明天下午", 0.95, "", false,
                "AUTO", "等待对方回复", "{}", "event-001", "2026-07-22T10:00:00Z",
                null, "", "2026-07-22T09:59:00Z", "2026-07-22T10:01:00Z"
        );
    }
}
