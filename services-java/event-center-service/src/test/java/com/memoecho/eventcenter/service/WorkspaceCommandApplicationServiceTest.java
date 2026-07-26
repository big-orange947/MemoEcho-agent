package com.memoecho.eventcenter.service;

import com.fasterxml.jackson.databind.JsonNode;
import com.fasterxml.jackson.databind.ObjectMapper;
import com.memoecho.eventcenter.dto.DispatchResult;
import com.memoecho.eventcenter.dto.EventIngestResponse;
import com.memoecho.eventcenter.dto.UnifiedEventPayload;
import com.memoecho.eventcenter.dto.WorkspaceCommandRequest;
import com.memoecho.eventcenter.dto.WorkspaceCommandResponse;
import org.junit.jupiter.api.Test;
import org.mockito.ArgumentCaptor;

import static org.assertj.core.api.Assertions.assertThat;
import static org.mockito.ArgumentMatchers.any;
import static org.mockito.Mockito.mock;
import static org.mockito.Mockito.verify;
import static org.mockito.Mockito.when;

class WorkspaceCommandApplicationServiceTest {

    private final ObjectMapper objectMapper = new ObjectMapper();
    private final EventCenterApplicationService eventCenterApplicationService = mock(EventCenterApplicationService.class);
    private final WorkspaceCommandApplicationService service = new WorkspaceCommandApplicationService(
            eventCenterApplicationService,
            objectMapper
    );

    @Test
    void shouldCreateDesktopEventAndReturnRuntimeResult() throws Exception {
        // 验证桌面命令会携带可信用户、显式路由，并进入标准事件链路。
        JsonNode runtimeBody = objectMapper.readTree("""
                {
                  "status": "success",
                  "route": "task_plan",
                  "summary": "Plan executed",
                  "final_reply": "今天先完成项目周报。",
                  "results": [
                    {
                      "agent": "work",
                      "status": "success",
                      "reply_draft": "今天先完成项目周报。",
                      "next_actions": ["查看待办"],
                      "need_confirmation": false
                    }
                  ]
                }
                """);
        when(eventCenterApplicationService.ingest(any())).thenReturn(new EventIngestResponse(
                "ignored-by-service",
                true,
                false,
                new DispatchResult(true, 200, runtimeBody, null),
                "accepted"
        ));

        WorkspaceCommandResponse response = service.execute(
                "user-001",
                new WorkspaceCommandRequest("今天应该先做什么？", "task_plan")
        );

        ArgumentCaptor<UnifiedEventPayload> eventCaptor = ArgumentCaptor.forClass(UnifiedEventPayload.class);
        verify(eventCenterApplicationService).ingest(eventCaptor.capture());
        UnifiedEventPayload event = eventCaptor.getValue();
        assertThat(event.platform()).isEqualTo("desktop");
        assertThat(event.eventType()).isEqualTo("desktop_command");
        assertThat(event.rawPayload().path("userId").asText()).isEqualTo("user-001");
        assertThat(event.rawPayload().path("requestedRoute").asText()).isEqualTo("task_plan");
        assertThat(event.rawPayload().path("allowTaskCreation").asBoolean()).isTrue();
        assertThat(response.status()).isEqualTo("success");
        assertThat(response.route()).isEqualTo("task_plan");
        assertThat(response.finalReply()).isEqualTo("今天先完成项目周报。");
        assertThat(response.results()).hasSize(1);
    }

    @Test
    void shouldReturnReadableFailureWhenRuntimeIsUnavailable() {
        // Runtime 不可用时，服务层要返回客户端可展示的失败信息，而不是抛出内部异常。
        when(eventCenterApplicationService.ingest(any())).thenReturn(new EventIngestResponse(
                "ignored-by-service",
                true,
                false,
                new DispatchResult(true, null, null, "Connection refused"),
                "accepted"
        ));

        WorkspaceCommandResponse response = service.execute(
                "user-001",
                new WorkspaceCommandRequest("总结最近消息", "chat_summary")
        );

        assertThat(response.status()).isEqualTo("failed");
        assertThat(response.error()).contains("Connection refused");
    }

    @Test
    void shouldLetRuntimeCreateDelegatedTasksFromWorkspaceCommand() throws Exception {
        // 委托任务创建由 Runtime/LangGraph 决策；Java 侧只负责放行桌面命令和标记允许创建任务。
        JsonNode runtimeBody = objectMapper.readTree("""
                {
                  "status": "success",
                  "route": "auto_route",
                  "summary": "Delegated task accepted",
                  "final_reply": "",
                  "results": []
                }
                """);
        when(eventCenterApplicationService.ingest(any())).thenReturn(new EventIngestResponse(
                "ignored-by-service",
                true,
                false,
                new DispatchResult(true, 200, runtimeBody, null),
                "accepted"
        ));

        service.execute(
                "user-001",
                new WorkspaceCommandRequest("帮我和km约一下明天下午的课程", "")
        );

        ArgumentCaptor<UnifiedEventPayload> eventCaptor = ArgumentCaptor.forClass(UnifiedEventPayload.class);
        verify(eventCenterApplicationService).ingest(eventCaptor.capture());
        UnifiedEventPayload event = eventCaptor.getValue();
        assertThat(event.eventType()).isEqualTo("desktop_command");
        assertThat(event.text()).isEqualTo("帮我和km约一下明天下午的课程");
        assertThat(event.rawPayload().path("allowTaskCreation").asBoolean()).isTrue();
    }
}
