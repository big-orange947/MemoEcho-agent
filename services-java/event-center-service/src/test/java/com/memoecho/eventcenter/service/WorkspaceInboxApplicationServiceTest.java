package com.memoecho.eventcenter.service;

import com.memoecho.eventcenter.dto.WorkspaceInboxItemResponse;
import com.memoecho.eventcenter.dto.WorkspaceInboxResponse;
import org.junit.jupiter.api.Test;

import java.util.List;

import static org.assertj.core.api.Assertions.assertThat;
import static org.mockito.BDDMockito.given;
import static org.mockito.Mockito.mock;
import static org.mockito.Mockito.verify;

class WorkspaceInboxApplicationServiceTest {

    @Test
    void shouldAggregateInboxCountsAndApplyLimit() {
        // 这个测试函数的作用是验证收件箱聚合服务会保留完整统计值，同时只返回前端请求数量以内的卡片。
        EventCenterApplicationService eventCenterApplicationService = mock(EventCenterApplicationService.class);
        WorkspaceInboxApplicationService service = new WorkspaceInboxApplicationService(eventCenterApplicationService);
        List<WorkspaceInboxItemResponse> items = List.of(
                item("event-new", "NEW", true),
                item("event-read", "READ", false),
                item("event-new-no-action", "NEW", false)
        );
        given(eventCenterApplicationService.findWorkspaceInboxItems(null, 200)).willReturn(items);

        WorkspaceInboxResponse response = service.buildInbox(null, 2);

        assertThat(response.inboxStatusFilter()).isEmpty();
        assertThat(response.totalCount()).isEqualTo(3);
        assertThat(response.newCount()).isEqualTo(2);
        assertThat(response.readCount()).isEqualTo(1);
        assertThat(response.actionRequiredCount()).isEqualTo(1);
        assertThat(response.items()).extracting(WorkspaceInboxItemResponse::eventId)
                .containsExactly("event-new", "event-read");
        verify(eventCenterApplicationService).findWorkspaceInboxItems(null, 200);
    }

    @Test
    void shouldNormalizeInvalidLimitAndForwardStatusFilter() {
        // 这个测试函数的作用是验证非法页大小会回退到默认值，并将用户选择的收件箱状态交给事件中心筛选。
        EventCenterApplicationService eventCenterApplicationService = mock(EventCenterApplicationService.class);
        WorkspaceInboxApplicationService service = new WorkspaceInboxApplicationService(eventCenterApplicationService);
        given(eventCenterApplicationService.findWorkspaceInboxItems("SNOOZED", 200))
                .willReturn(List.of(item("event-snoozed", "SNOOZED", false)));

        WorkspaceInboxResponse response = service.buildInbox("SNOOZED", 0);

        assertThat(response.inboxStatusFilter()).isEqualTo("SNOOZED");
        assertThat(response.items()).hasSize(1);
        assertThat(response.items().getFirst().inboxStatus()).isEqualTo("SNOOZED");
        verify(eventCenterApplicationService).findWorkspaceInboxItems("SNOOZED", 200);
    }

    /**
     * 创建最小化的收件箱卡片，避免每个测试重复无关字段。
     */
    private WorkspaceInboxItemResponse item(String eventId, String inboxStatus, boolean actionRequired) {
        return new WorkspaceInboxItemResponse(
                eventId,
                "qq",
                "group",
                "1098307542",
                "Memo Echo 项目小组",
                "2597164807",
                "freeze",
                "测试消息",
                "2026-07-10T08:00:00Z",
                "message_dispatch",
                "PROCESSED",
                "SILENT",
                "",
                actionRequired,
                actionRequired,
                inboxStatus,
                null,
                "",
                null
        );
    }
}
