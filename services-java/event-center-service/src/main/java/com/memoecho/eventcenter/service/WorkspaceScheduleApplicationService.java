package com.memoecho.eventcenter.service;

import com.memoecho.eventcenter.dto.ConversationMessageResponse;
import com.memoecho.eventcenter.dto.ScheduleServiceCreateRequest;
import com.memoecho.eventcenter.dto.ScheduleServiceScheduleResponse;
import com.memoecho.eventcenter.dto.WorkspaceScheduleCreateRequest;
import com.memoecho.eventcenter.dto.WorkspaceScheduleSourceContextResponse;
import org.springframework.http.HttpStatus;
import org.springframework.stereotype.Service;
import org.springframework.web.server.ResponseStatusException;

import java.util.List;
import java.util.UUID;

@Service
public class WorkspaceScheduleApplicationService {

    private final ScheduleServiceQueryClient scheduleServiceClient;
    private final EventCenterApplicationService eventCenterApplicationService;

    public WorkspaceScheduleApplicationService(
            ScheduleServiceQueryClient scheduleServiceClient,
            EventCenterApplicationService eventCenterApplicationService
    ) {
        // 这个构造函数的作用是组合日程服务与事件仓库，让工作台操作同时具备存储能力和来源鉴权。
        this.scheduleServiceClient = scheduleServiceClient;
        this.eventCenterApplicationService = eventCenterApplicationService;
    }

    public ScheduleServiceScheduleResponse createManualSchedule(
            String ownerUserId,
            WorkspaceScheduleCreateRequest request
    ) {
        // 这个函数的作用是为手动日程生成不可碰撞且带用户归属的来源 ID，再交给 schedule-service 保存。
        if (request.endTime() != null && request.endTime().isBefore(request.startTime())) {
            throw new ResponseStatusException(HttpStatus.BAD_REQUEST, "日程结束时间不能早于开始时间。");
        }
        String normalizedContent = request.content() == null || request.content().isBlank()
                ? request.title().trim()
                : request.content().trim();
        return scheduleServiceClient.createSchedule(new ScheduleServiceCreateRequest(
                manualSourcePrefix(ownerUserId) + UUID.randomUUID(),
                "local",
                "manual",
                ownerUserId,
                request.title().trim(),
                request.startTime(),
                request.endTime(),
                normalizeOptional(request.location()),
                normalizedContent,
                null,
                "manual"
        ));
    }

    public void deleteSchedule(String ownerUserId, String scheduleId) {
        // 这个函数的作用是先验证日程归属再执行删除，防止仅凭 UUID 操作其他账户的日程。
        requireOwnedSchedule(ownerUserId, scheduleId);
        if (!scheduleServiceClient.deleteSchedule(scheduleId)) {
            throw new ResponseStatusException(HttpStatus.NOT_FOUND, "日程不存在或已过期。");
        }
    }

    public WorkspaceScheduleSourceContextResponse getSourceContext(
            String ownerUserId,
            String scheduleId,
            Integer radius
    ) {
        // 这个函数的作用是把日程来源转换为前端可展示的会话信息和有限上下文片段。
        ScheduleServiceScheduleResponse schedule = requireOwnedSchedule(ownerUserId, scheduleId);
        if (isOwnedManualSchedule(ownerUserId, schedule)) {
            return new WorkspaceScheduleSourceContextResponse(
                    schedule.id(),
                    schedule.title(),
                    "manual",
                    schedule.sourceEventId(),
                    schedule.platform(),
                    "manual",
                    schedule.chatId(),
                    "手动创建",
                    false,
                    List.of()
            );
        }

        ConversationMessageResponse sourceMessage = eventCenterApplicationService
                .findOwnedSourceMessage(ownerUserId, schedule.sourceEventId())
                .orElseThrow(() -> new ResponseStatusException(HttpStatus.NOT_FOUND, "找不到该日程对应的原始消息。"));
        List<ConversationMessageResponse> messages = eventCenterApplicationService
                .findConversationContextAroundEvent(ownerUserId, schedule.sourceEventId(), radius);
        return new WorkspaceScheduleSourceContextResponse(
                schedule.id(),
                schedule.title(),
                "conversation",
                schedule.sourceEventId(),
                sourceMessage.platform(),
                sourceMessage.chatType(),
                sourceMessage.chatId(),
                sourceMessage.chatName(),
                true,
                messages
        );
    }

    private ScheduleServiceScheduleResponse requireOwnedSchedule(String ownerUserId, String scheduleId) {
        // 这个函数的作用是集中处理日程不存在和归属不匹配，外部统一看到 404，避免泄漏记录是否存在。
        ScheduleServiceScheduleResponse schedule = scheduleServiceClient.getSchedule(scheduleId)
                .orElseThrow(() -> new ResponseStatusException(HttpStatus.NOT_FOUND, "日程不存在或已过期。"));
        if (isOwnedManualSchedule(ownerUserId, schedule)) {
            return schedule;
        }
        boolean ownsSource = eventCenterApplicationService
                .findOwnedSourceMessage(ownerUserId, schedule.sourceEventId())
                .isPresent();
        if (!ownsSource) {
            throw new ResponseStatusException(HttpStatus.NOT_FOUND, "日程不存在或不属于当前账户。");
        }
        return schedule;
    }

    private boolean isOwnedManualSchedule(String ownerUserId, ScheduleServiceScheduleResponse schedule) {
        // 手动日程通过不可伪造的当前用户前缀和 senderId 双重确认归属。
        return "local".equalsIgnoreCase(schedule.platform())
                && schedule.sourceEventId() != null
                && schedule.sourceEventId().startsWith(manualSourcePrefix(ownerUserId))
                && ownerUserId.equals(schedule.senderId());
    }

    private String manualSourcePrefix(String ownerUserId) {
        // 这个函数的作用是生成稳定的手动来源命名空间，后续无需额外数据表也能完成归属校验。
        return "manual:" + ownerUserId + ":";
    }

    private String normalizeOptional(String value) {
        // 这个函数的作用是清理可选文本，避免只包含空白字符的数据进入下游。
        return value == null || value.isBlank() ? null : value.trim();
    }
}
