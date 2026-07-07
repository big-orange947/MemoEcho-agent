package com.memoecho.task.controller;

import com.memoecho.task.dto.CreateTaskRequest;
import com.memoecho.task.dto.TaskItemResponse;
import com.memoecho.task.service.TaskApplicationService;
import jakarta.validation.Valid;
import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.PostMapping;
import org.springframework.web.bind.annotation.RequestBody;
import org.springframework.web.bind.annotation.RequestMapping;
import org.springframework.web.bind.annotation.RequestParam;
import org.springframework.web.bind.annotation.RestController;

import java.util.List;

@RestController
@RequestMapping("/internal/tasks")
public class InternalTaskController {

    private final TaskApplicationService taskApplicationService;

    public InternalTaskController(TaskApplicationService taskApplicationService) {
        this.taskApplicationService = taskApplicationService;
    }

    @PostMapping
    public TaskItemResponse createTask(@Valid @RequestBody CreateTaskRequest request) {
        // Controller 只做参数接收和校验，业务规则继续放在 service 里。
        return taskApplicationService.create(request);
    }

    @GetMapping
    public List<TaskItemResponse> listTasks(
            @RequestParam(required = false) String chatId,
            @RequestParam(required = false) String senderId,
            @RequestParam(required = false) String sourceEventId,
            @RequestParam(required = false) String status
    ) {
        // 这里目前只做筛选，不急着做分页和排序，
        // 后面扩展时也不会影响创建接口契约。
        return taskApplicationService.list(chatId, senderId, sourceEventId, status);
    }
}
