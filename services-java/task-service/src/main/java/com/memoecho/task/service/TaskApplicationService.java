package com.memoecho.task.service;

import com.memoecho.task.dto.CreateTaskRequest;
import com.memoecho.task.dto.TaskItemResponse;
import com.memoecho.task.model.TaskItem;
import com.memoecho.task.repository.TaskRepository;
import org.springframework.stereotype.Service;

import java.time.LocalDateTime;
import java.util.List;
import java.util.UUID;

@Service
public class TaskApplicationService {

    private final TaskRepository taskRepository;

    public TaskApplicationService(TaskRepository taskRepository) {
        this.taskRepository = taskRepository;
    }

    public TaskItemResponse create(CreateTaskRequest request) {
        // 幂等依据是上游事件 ID，这样重复投递或重试时不会插入重复任务。
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

    public List<TaskItemResponse> list(String chatId, String senderId, String sourceEventId, String status) {
        // 现在用的是内存仓库，所以筛选逻辑先放在 service 层。
        // 以后切到 MySQL 之类的存储，再把这部分下沉到 repository。
        return taskRepository.findAll().stream()
                .filter(item -> chatId == null || chatId.isBlank() || item.chatId().equals(chatId))
                .filter(item -> senderId == null || senderId.isBlank() || item.senderId().equals(senderId))
                .filter(item -> sourceEventId == null || sourceEventId.isBlank() || item.sourceEventId().equals(sourceEventId))
                .filter(item -> status == null || status.isBlank() || item.status().equalsIgnoreCase(status))
                .map(this::toResponse)
                .toList();
    }

    private TaskItemResponse toResponse(TaskItem item) {
        // 单独保留映射方法，避免 controller 和业务逻辑依赖内部存储结构。
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
