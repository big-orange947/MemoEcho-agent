package com.memoecho.eventcenter.service;

import com.memoecho.eventcenter.dto.ConversationSummaryResponse;
import com.memoecho.eventcenter.dto.ScheduleServiceScheduleResponse;
import com.memoecho.eventcenter.dto.TaskServiceTaskResponse;
import com.memoecho.eventcenter.dto.WorkspaceBriefingOverviewResponse;
import com.memoecho.eventcenter.dto.WorkspaceBriefingResponse;
import com.memoecho.eventcenter.dto.WorkspaceConversationDigestResponse;
import com.memoecho.eventcenter.dto.WorkspaceScheduleDigestResponse;
import com.memoecho.eventcenter.dto.WorkspaceSuggestedActionResponse;
import com.memoecho.eventcenter.dto.WorkspaceTaskDigestResponse;
import org.springframework.stereotype.Service;

import java.time.Duration;
import java.time.Instant;
import java.time.LocalDate;
import java.util.ArrayList;
import java.util.Comparator;
import java.util.List;

@Service
public class WorkspaceBriefingApplicationService {

    private final EventCenterApplicationService eventCenterApplicationService;
    private final TaskServiceQueryClient taskServiceQueryClient;
    private final ScheduleServiceQueryClient scheduleServiceQueryClient;

    public WorkspaceBriefingApplicationService(
            EventCenterApplicationService eventCenterApplicationService,
            TaskServiceQueryClient taskServiceQueryClient,
            ScheduleServiceQueryClient scheduleServiceQueryClient
    ) {
        // 这个构造函数的作用是注入消息、任务、日程三条数据源，供工作台摘要聚合统一使用。
        this.eventCenterApplicationService = eventCenterApplicationService;
        this.taskServiceQueryClient = taskServiceQueryClient;
        this.scheduleServiceQueryClient = scheduleServiceQueryClient;
    }

    public WorkspaceBriefingResponse buildBriefing(
            String userName,
            String senderId,
            Integer lookbackMinutes,
            Integer conversationLimit,
            Integer taskLimit,
            Integer scheduleLimit
    ) {
        // 这个函数的作用是生成前端首页可直接消费的登录摘要包。
        int safeLookbackMinutes = lookbackMinutes == null || lookbackMinutes <= 0 ? 480 : Math.min(lookbackMinutes, 10080);
        int safeConversationLimit = conversationLimit == null || conversationLimit <= 0 ? 5 : Math.min(conversationLimit, 20);
        int safeTaskLimit = taskLimit == null || taskLimit <= 0 ? 5 : Math.min(taskLimit, 20);
        int safeScheduleLimit = scheduleLimit == null || scheduleLimit <= 0 ? 5 : Math.min(scheduleLimit, 20);

        List<ConversationSummaryResponse> conversations = eventCenterApplicationService.findConversationSummaries(
                null,
                null,
                null,
                null,
                safeLookbackMinutes
        );
        List<TaskServiceTaskResponse> tasks = taskServiceQueryClient.listPendingTasks(senderId, safeTaskLimit);
        List<ScheduleServiceScheduleResponse> schedules = scheduleServiceQueryClient.listSchedules(senderId);

        List<WorkspaceConversationDigestResponse> importantConversations = selectImportantConversations(conversations, safeConversationLimit);
        List<WorkspaceTaskDigestResponse> pendingTasks = tasks.stream()
                .limit(safeTaskLimit)
                .map(this::toTaskDigest)
                .toList();
        List<WorkspaceScheduleDigestResponse> todaySchedules = selectTodaySchedules(schedules, safeScheduleLimit);
        int actionRequiredCount = (int) importantConversations.stream()
                .filter(WorkspaceConversationDigestResponse::actionRequired)
                .count();
        List<WorkspaceSuggestedActionResponse> suggestedActions = buildSuggestedActions(tasks, schedules, importantConversations);

        WorkspaceBriefingOverviewResponse overview = new WorkspaceBriefingOverviewResponse(
                buildOpeningLine(userName, safeLookbackMinutes, importantConversations.size(), pendingTasks.size(), todaySchedules.size()),
                buildSuggestedStart(tasks, todaySchedules, importantConversations),
                importantConversations.size(),
                pendingTasks.size(),
                todaySchedules.size(),
                actionRequiredCount
        );

        return new WorkspaceBriefingResponse(
                Instant.now().toString(),
                safeLookbackMinutes,
                overview,
                importantConversations,
                pendingTasks,
                todaySchedules,
                suggestedActions
        );
    }

    private List<WorkspaceConversationDigestResponse> selectImportantConversations(
            List<ConversationSummaryResponse> conversations,
            int limit
    ) {
        // 这个函数的作用是从最近活跃会话中挑出最值得优先查看的消息来源。
        return conversations.stream()
                .sorted(Comparator
                        .comparing((ConversationSummaryResponse item) -> !item.actionRequired())
                        .thenComparing(item -> !"urgent".equalsIgnoreCase(item.lastDispatchMode()))
                        .thenComparing(this::parseConversationInstant, Comparator.reverseOrder()))
                .limit(limit)
                .map(this::toConversationDigest)
                .toList();
    }

    private List<WorkspaceScheduleDigestResponse> selectTodaySchedules(
            List<ScheduleServiceScheduleResponse> schedules,
            int limit
    ) {
        // 这个函数的作用是筛出今天相关的日程，优先给登录后的工作台展示当天安排。
        LocalDate today = LocalDate.now();
        return schedules.stream()
                .filter(item -> item.startTime() != null && item.startTime().toLocalDate().isEqual(today))
                .sorted(Comparator.comparing(ScheduleServiceScheduleResponse::startTime))
                .limit(limit)
                .map(this::toScheduleDigest)
                .toList();
    }

    private List<WorkspaceSuggestedActionResponse> buildSuggestedActions(
            List<TaskServiceTaskResponse> tasks,
            List<ScheduleServiceScheduleResponse> schedules,
            List<WorkspaceConversationDigestResponse> importantConversations
    ) {
        // 这个函数的作用是根据任务、日程和重点会话生成建议动作，帮助用户打开首页后马上进入执行。
        List<WorkspaceSuggestedActionResponse> actions = new ArrayList<>();
        LocalDate today = LocalDate.now();

        importantConversations.stream()
                .filter(WorkspaceConversationDigestResponse::actionRequired)
                .findFirst()
                .ifPresent(item -> actions.add(new WorkspaceSuggestedActionResponse(
                        "conversation_action_required",
                        "先处理需要确认的会话",
                        "这条会话已经生成草稿、等待确认，或处理链路出现异常，建议先打开检查。",
                        item.chatId()
                )));

        tasks.stream()
                .filter(item -> item.dueTime() != null && !item.dueTime().toLocalDate().isAfter(today))
                .findFirst()
                .ifPresent(item -> actions.add(new WorkspaceSuggestedActionResponse(
                        "task",
                        "优先处理待办任务",
                        "这项任务已经到期或今天截止，建议你先把它完成。",
                        item.id()
                )));

        schedules.stream()
                .filter(item -> item.startTime() != null && item.startTime().toLocalDate().isEqual(today))
                .findFirst()
                .ifPresent(item -> actions.add(new WorkspaceSuggestedActionResponse(
                        "schedule",
                        "先确认今天的日程安排",
                        "今天已经有明确日程，建议先看时间和地点避免冲突。",
                        item.id()
                )));

        importantConversations.stream()
                .filter(item -> "urgent".equalsIgnoreCase(item.dispatchMode()))
                .findFirst()
                .ifPresent(item -> actions.add(new WorkspaceSuggestedActionResponse(
                        "conversation",
                        "优先查看重点消息",
                        "这条会话被判定为高优先级，可能包含通知、截止时间或需要你及时处理的信息。",
                        item.chatId()
                )));

        if (actions.isEmpty()) {
            actions.add(new WorkspaceSuggestedActionResponse(
                    "overview",
                    "先浏览最近摘要",
                    "当前没有明显紧急事项，建议先快速浏览离线期间的重要消息。",
                    "briefing-overview"
            ));
        }

        return actions;
    }

    private WorkspaceConversationDigestResponse toConversationDigest(ConversationSummaryResponse summary) {
        // 这个函数的作用是把会话摘要映射成前端工作台可直接展示的重点会话卡片。
        return new WorkspaceConversationDigestResponse(
                summary.platform(),
                summary.chatType(),
                summary.chatId(),
                summary.chatName(),
                summary.lastSenderName(),
                summary.lastMessage(),
                summary.lastMessageTime(),
                summary.lastDispatchMode(),
                deriveConversationHighlightReason(summary),
                summary.lastProcessingStatus(),
                summary.lastWriteBackStatus(),
                summary.actionRequired()
        );
    }

    private WorkspaceTaskDigestResponse toTaskDigest(TaskServiceTaskResponse task) {
        // 这个函数的作用是把待办任务映射成工作台右侧待办卡片需要的精简结构。
        return new WorkspaceTaskDigestResponse(
                task.id(),
                task.title(),
                task.description(),
                task.priority(),
                task.status(),
                task.dueTime()
        );
    }

    private WorkspaceScheduleDigestResponse toScheduleDigest(ScheduleServiceScheduleResponse schedule) {
        // 这个函数的作用是把日程记录映射成前端工作台当日日程卡片。
        return new WorkspaceScheduleDigestResponse(
                schedule.id(),
                schedule.title(),
                schedule.startTime(),
                schedule.endTime(),
                schedule.location(),
                schedule.content()
        );
    }

    private String buildOpeningLine(
            String userName,
            int lookbackMinutes,
            int importantConversationCount,
            int pendingTaskCount,
            int todayScheduleCount
    ) {
        // 这个函数的作用是生成首页顶部欢迎语，直接对应“你不在的这段时间里发生了什么”的产品表达。
        String resolvedName = userName == null || userName.isBlank() ? "朋友" : userName;
        long hours = Math.max(1, Duration.ofMinutes(lookbackMinutes).toHours());
        return "Hi, " + resolvedName + "，你离开的这 " + hours + " 小时里，我帮你整理出 "
                + importantConversationCount + " 条重点消息、"
                + pendingTaskCount + " 条待办和 "
                + todayScheduleCount + " 条今天的日程。";
    }

    private String buildSuggestedStart(
            List<TaskServiceTaskResponse> tasks,
            List<WorkspaceScheduleDigestResponse> todaySchedules,
            List<WorkspaceConversationDigestResponse> importantConversations
    ) {
        // 这个函数的作用是给首页生成一句“建议你先从什么开始”的总建议。
        LocalDate today = LocalDate.now();

        for (TaskServiceTaskResponse task : tasks) {
            if (task.dueTime() != null && !task.dueTime().toLocalDate().isAfter(today)) {
                return "建议你先从待办“" + task.title() + "”开始，它已经到期或今天截止。";
            }
        }
        if (!todaySchedules.isEmpty()) {
            return "建议你先确认今天的日程“" + todaySchedules.get(0).title() + "”，避免后续时间冲突。";
        }
        if (!importantConversations.isEmpty()) {
            return "建议你先查看“" + importantConversations.get(0).chatName() + "”的重点消息。";
        }
        return "当前没有明显紧急事项，建议你先浏览最近摘要。";
    }

    private String deriveConversationHighlightReason(ConversationSummaryResponse summary) {
        // 这个函数的作用是给重点会话补一句为什么会被选中的说明，方便前端直接展示。
        if ("urgent".equalsIgnoreCase(summary.lastDispatchMode())) {
            return "这条会话被判定为高优先级，可能包含通知、截止时间或需要即时关注的信息。";
        }
        if ("group".equalsIgnoreCase(summary.chatType())) {
            return "这是最近较活跃的群聊，建议快速了解群里最新进展。";
        }
        return "这是你最近有更新的私聊会话，建议查看最新消息。";
    }

    private Instant parseConversationInstant(ConversationSummaryResponse summary) {
        // 这个函数的作用是把会话时间转成 Instant，便于做统一排序。
        try {
            return Instant.parse(summary.lastMessageTime());
        } catch (Exception ignored) {
            return Instant.EPOCH;
        }
    }
}
