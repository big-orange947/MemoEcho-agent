package com.memoecho.task.service;

import com.memoecho.task.dto.CreateTaskRequest;
import com.memoecho.task.dto.TaskItemResponse;
import com.memoecho.task.model.TaskItem;
import com.memoecho.task.repository.TaskRepository;
import org.springframework.stereotype.Service;

import java.time.LocalDate;
import java.time.LocalDateTime;
import java.util.Comparator;
import java.util.List;
import java.util.UUID;

@Service
public class TaskApplicationService {

    private final TaskRepository taskRepository;

    public TaskApplicationService(TaskRepository taskRepository) {
        // 这个构造函数的作用是注入任务仓储，方便后续从内存实现平滑切换到数据库实现。
        this.taskRepository = taskRepository;
    }

    public TaskItemResponse create(CreateTaskRequest request) {
        // 这个函数的作用是基于 sourceEventId 做幂等创建，避免消息重试时生成重复任务。
        TaskItem existing = taskRepository.findBySourceEventId(request.sourceEventId()).orElse(null);
        if (existing != null) {
            return toResponse(existing);
        }

        TaskItem item = new TaskItem(
                UUID.randomUUID().toString(),
                request.sourceEventId(),
                request.platform(),
                request.chatId(),
                request.senderId(),
                request.title(),
                request.description(),
                request.dueTime(),
                request.priority(),
                request.status(),
                request.confidence(),
                LocalDateTime.now()
        );
        return toResponse(taskRepository.save(item));
    }

    public List<TaskItemResponse> list(
            String chatId,
            String senderId,
            String sourceEventId,
            String status,
            String priority,
            boolean todayOnly,
            boolean onlyPending,
            LocalDateTime dueFrom,
            LocalDateTime dueTo,
            Integer limit
    ) {
        // 这个函数的作用是聚合任务查询规则，优先给 Python runtime 返回可直接拿来生成计划的有序结果。
        LocalDate today = LocalDate.now();

        return taskRepository.findAll().stream()
                .filter(item -> matchesText(item.chatId(), chatId))
                .filter(item -> matchesText(item.senderId(), senderId))
                .filter(item -> matchesText(item.sourceEventId(), sourceEventId))
                .filter(item -> matchesStatus(item.status(), status, onlyPending))
                .filter(item -> matchesText(item.priority(), priority))
                .filter(item -> matchesToday(item, todayOnly, today))
                .filter(item -> matchesDueRange(item, dueFrom, dueTo))
                .sorted(taskQueryComparator(today))
                .limit(resolveLimit(limit))
                .map(this::toResponse)
                .toList();
    }

    private boolean matchesText(String actual, String expected) {
        // 这个函数的作用是统一处理字符串型筛选条件，空值时默认不过滤。
        return expected == null || expected.isBlank() || actual.equals(expected);
    }

    private boolean matchesStatus(String actualStatus, String expectedStatus, boolean onlyPending) {
        // 这个函数的作用是兼容显式 status 查询和 onlyPending 快捷筛选。
        if (expectedStatus != null && !expectedStatus.isBlank()) {
            return actualStatus.equalsIgnoreCase(expectedStatus);
        }
        if (onlyPending) {
            return actualStatus.equalsIgnoreCase("pending");
        }
        return true;
    }

    private boolean matchesToday(TaskItem item, boolean todayOnly, LocalDate today) {
        // 这个函数的作用是在 todayOnly 模式下只保留截止日期落在今天的任务。
        if (!todayOnly) {
            return true;
        }
        return item.dueTime() != null && item.dueTime().toLocalDate().isEqual(today);
    }

    private boolean matchesDueRange(TaskItem item, LocalDateTime dueFrom, LocalDateTime dueTo) {
        // 这个函数的作用是按截止时间范围筛选任务，没有截止时间的任务只在未设置范围时保留。
        if (dueFrom == null && dueTo == null) {
            return true;
        }
        if (item.dueTime() == null) {
            return false;
        }
        if (dueFrom != null && item.dueTime().isBefore(dueFrom)) {
            return false;
        }
        if (dueTo != null && item.dueTime().isAfter(dueTo)) {
            return false;
        }
        return true;
    }

    private Comparator<TaskItem> taskQueryComparator(LocalDate today) {
        // 这个函数的作用是把待办且更紧急的任务排在前面，方便上游直接取前几个生成今日计划。
        return Comparator
                .comparing((TaskItem item) -> !item.status().equalsIgnoreCase("pending"))
                .thenComparing((TaskItem item) -> isOverdue(item, today) ? 0 : 1)
                .thenComparing(TaskItem::dueTime, Comparator.nullsLast(LocalDateTime::compareTo))
                .thenComparing(TaskItem::createdAt, Comparator.reverseOrder());
    }

    private boolean isOverdue(TaskItem item, LocalDate today) {
        // 这个函数的作用是判断任务是否已逾期，逾期任务会在排序时被提前暴露出来。
        return item.dueTime() != null && item.dueTime().toLocalDate().isBefore(today);
    }

    private long resolveLimit(Integer limit) {
        // 这个函数的作用是兜底限制返回数量，避免上游一次性拉取过多任务。
        if (limit == null || limit <= 0) {
            return 20;
        }
        return Math.min(limit, 100);
    }

    private TaskItemResponse toResponse(TaskItem item) {
        // 这个函数的作用是隔离内部存储模型，避免控制层和调用方直接依赖实体结构。
        return new TaskItemResponse(
                item.id(),
                item.sourceEventId(),
                item.platform(),
                item.chatId(),
                item.senderId(),
                item.title(),
                item.description(),
                item.dueTime(),
                item.priority(),
                item.status(),
                item.confidence(),
                item.createdAt()
        );
    }
}
