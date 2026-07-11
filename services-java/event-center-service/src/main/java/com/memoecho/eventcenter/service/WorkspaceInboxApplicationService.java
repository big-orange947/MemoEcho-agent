package com.memoecho.eventcenter.service;

import com.memoecho.eventcenter.dto.WorkspaceInboxItemResponse;
import com.memoecho.eventcenter.dto.WorkspaceInboxResponse;
import org.springframework.stereotype.Service;

import java.time.Instant;
import java.util.List;

@Service
public class WorkspaceInboxApplicationService {

    private final EventCenterApplicationService eventCenterApplicationService;

    public WorkspaceInboxApplicationService(EventCenterApplicationService eventCenterApplicationService) {
        // 这个构造函数的作用是注入事件中心查询能力，把底层事件记录聚合为前端可直接消费的收件箱模型。
        this.eventCenterApplicationService = eventCenterApplicationService;
    }

    public WorkspaceInboxResponse buildInbox(String inboxStatus, Integer limit) {
        // 这个函数的作用是生成工作台收件箱快照，默认只显示仍需关注的消息，同时给出状态计数方便前端绘制筛选标签。
        int safeLimit = limit == null || limit <= 0 ? 50 : Math.min(limit, 200);
        List<WorkspaceInboxItemResponse> allItems = eventCenterApplicationService.findWorkspaceInboxItems(inboxStatus, 200);
        List<WorkspaceInboxItemResponse> visibleItems = allItems.stream().limit(safeLimit).toList();

        return new WorkspaceInboxResponse(
                Instant.now().toString(),
                inboxStatus == null ? "" : inboxStatus,
                allItems.size(),
                (int) allItems.stream().filter(item -> "NEW".equals(item.inboxStatus())).count(),
                (int) allItems.stream().filter(item -> "READ".equals(item.inboxStatus())).count(),
                (int) allItems.stream().filter(WorkspaceInboxItemResponse::actionRequired).count(),
                visibleItems
        );
    }
}
