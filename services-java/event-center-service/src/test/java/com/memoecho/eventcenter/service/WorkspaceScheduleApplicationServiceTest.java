package com.memoecho.eventcenter.service;

import com.memoecho.eventcenter.dto.ConversationMessageResponse;
import com.memoecho.eventcenter.dto.ScheduleServiceCreateRequest;
import com.memoecho.eventcenter.dto.ScheduleServiceScheduleResponse;
import com.memoecho.eventcenter.dto.WorkspaceScheduleCreateRequest;
import org.junit.jupiter.api.Test;
import org.mockito.ArgumentCaptor;
import org.springframework.web.server.ResponseStatusException;

import java.time.LocalDateTime;
import java.util.List;
import java.util.Optional;

import static org.assertj.core.api.Assertions.assertThat;
import static org.assertj.core.api.Assertions.assertThatThrownBy;
import static org.mockito.BDDMockito.given;
import static org.mockito.Mockito.mock;
import static org.mockito.Mockito.verify;

class WorkspaceScheduleApplicationServiceTest {

    @Test
    void shouldCreateManualScheduleInCurrentUserNamespace() {
        // 这个测试验证手动日程不会伪装成聊天来源，并且来源 ID 明确绑定当前登录用户。
        ScheduleServiceQueryClient scheduleClient = mock(ScheduleServiceQueryClient.class);
        EventCenterApplicationService eventCenterService = mock(EventCenterApplicationService.class);
        WorkspaceScheduleApplicationService service = new WorkspaceScheduleApplicationService(
                scheduleClient, eventCenterService
        );
        LocalDateTime startTime = LocalDateTime.of(2026, 7, 18, 14, 0);
        WorkspaceScheduleCreateRequest request = new WorkspaceScheduleCreateRequest(
                "项目例会", startTime, startTime.plusHours(1), "A01-N105", "讨论客户端进度"
        );
        given(scheduleClient.createSchedule(org.mockito.ArgumentMatchers.any()))
                .willAnswer(invocation -> responseFrom(invocation.getArgument(0)));

        ScheduleServiceScheduleResponse response = service.createManualSchedule("user-001", request);

        ArgumentCaptor<ScheduleServiceCreateRequest> captor = ArgumentCaptor.forClass(ScheduleServiceCreateRequest.class);
        verify(scheduleClient).createSchedule(captor.capture());
        assertThat(captor.getValue().sourceEventId()).startsWith("manual:user-001:");
        assertThat(captor.getValue().platform()).isEqualTo("local");
        assertThat(captor.getValue().senderId()).isEqualTo("user-001");
        assertThat(response.title()).isEqualTo("项目例会");
    }

    @Test
    void shouldDeleteOnlyScheduleOwnedBySourceEvent() {
        // 这个测试验证自动抽取日程必须能追溯到当前用户拥有的来源事件，才允许执行删除。
        ScheduleServiceQueryClient scheduleClient = mock(ScheduleServiceQueryClient.class);
        EventCenterApplicationService eventCenterService = mock(EventCenterApplicationService.class);
        WorkspaceScheduleApplicationService service = new WorkspaceScheduleApplicationService(
                scheduleClient, eventCenterService
        );
        ScheduleServiceScheduleResponse schedule = autoSchedule("schedule-1", "source-event-1");
        given(scheduleClient.getSchedule("schedule-1")).willReturn(Optional.of(schedule));
        given(eventCenterService.findOwnedSourceMessage("user-001", "source-event-1"))
                .willReturn(Optional.of(sourceMessage("source-event-1")));
        given(scheduleClient.deleteSchedule("schedule-1")).willReturn(true);

        service.deleteSchedule("user-001", "schedule-1");

        verify(scheduleClient).deleteSchedule("schedule-1");
    }

    @Test
    void shouldRejectScheduleWithoutOwnedSource() {
        // 这个测试验证来源事件不属于当前用户时统一返回 404，不能泄漏日程是否真实存在。
        ScheduleServiceQueryClient scheduleClient = mock(ScheduleServiceQueryClient.class);
        EventCenterApplicationService eventCenterService = mock(EventCenterApplicationService.class);
        WorkspaceScheduleApplicationService service = new WorkspaceScheduleApplicationService(
                scheduleClient, eventCenterService
        );
        given(scheduleClient.getSchedule("schedule-2"))
                .willReturn(Optional.of(autoSchedule("schedule-2", "foreign-event")));
        given(eventCenterService.findOwnedSourceMessage("user-001", "foreign-event"))
                .willReturn(Optional.empty());

        assertThatThrownBy(() -> service.deleteSchedule("user-001", "schedule-2"))
                .isInstanceOf(ResponseStatusException.class)
                .hasMessageContaining("404 NOT_FOUND");
    }

    @Test
    void shouldReturnConversationSourceContext() {
        // 这个测试验证来源详情会携带会话名称和有限消息片段，供客户端弹窗直接展示。
        ScheduleServiceQueryClient scheduleClient = mock(ScheduleServiceQueryClient.class);
        EventCenterApplicationService eventCenterService = mock(EventCenterApplicationService.class);
        WorkspaceScheduleApplicationService service = new WorkspaceScheduleApplicationService(
                scheduleClient, eventCenterService
        );
        ScheduleServiceScheduleResponse schedule = autoSchedule("schedule-3", "source-event-3");
        ConversationMessageResponse source = sourceMessage("source-event-3");
        given(scheduleClient.getSchedule("schedule-3")).willReturn(Optional.of(schedule));
        given(eventCenterService.findOwnedSourceMessage("user-001", "source-event-3"))
                .willReturn(Optional.of(source));
        given(eventCenterService.findConversationContextAroundEvent("user-001", "source-event-3", 3))
                .willReturn(List.of(source));

        var response = service.getSourceContext("user-001", "schedule-3", 3);

        assertThat(response.sourceType()).isEqualTo("conversation");
        assertThat(response.chatName()).isEqualTo("Memo Echo 项目群");
        assertThat(response.messages()).extracting(ConversationMessageResponse::eventId)
                .containsExactly("source-event-3");
    }

    private ScheduleServiceScheduleResponse responseFrom(ScheduleServiceCreateRequest request) {
        // 这个函数的作用是把下游创建请求映射成测试响应，避免测试依赖真实 HTTP 服务。
        return new ScheduleServiceScheduleResponse(
                "schedule-manual", request.sourceEventId(), request.platform(), request.chatId(), request.senderId(),
                request.title(), request.startTime(), request.endTime(), request.location(), request.content(),
                request.participants(), request.confidence(), LocalDateTime.now()
        );
    }

    private ScheduleServiceScheduleResponse autoSchedule(String id, String sourceEventId) {
        // 这个函数的作用是构造一条来自 QQ 会话的自动提取日程。
        LocalDateTime startTime = LocalDateTime.of(2026, 7, 18, 14, 0);
        return new ScheduleServiceScheduleResponse(
                id, sourceEventId, "qq", "1098307542", "2597164807", "项目例会",
                startTime, startTime.plusHours(1), "A01-N105", "讨论项目进度", null, "high",
                LocalDateTime.of(2026, 7, 15, 10, 0)
        );
    }

    private ConversationMessageResponse sourceMessage(String eventId) {
        // 这个函数的作用是构造来源上下文中被高亮的原始聊天消息。
        return new ConversationMessageResponse(
                eventId, "qq", "group", "1098307542", "Memo Echo 项目群",
                "2597164807", "freeze", "member", "明天下午两点开项目例会",
                "2026-07-17T06:00:00Z", List.of(), List.of(), true, false,
                "schedule_extract", "urgent", "PROCESSED", "已提取日程", "NONE",
                false, "", "READ", null
        );
    }
}
