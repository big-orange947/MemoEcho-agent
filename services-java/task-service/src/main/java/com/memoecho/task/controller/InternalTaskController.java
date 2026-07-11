package com.memoecho.task.controller;

import com.memoecho.task.dto.CreateTaskRequest;
import com.memoecho.task.dto.TaskItemResponse;
import com.memoecho.task.service.TaskApplicationService;
import jakarta.validation.Valid;
import org.springframework.format.annotation.DateTimeFormat;
import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.PostMapping;
import org.springframework.web.bind.annotation.RequestBody;
import org.springframework.web.bind.annotation.RequestMapping;
import org.springframework.web.bind.annotation.RequestParam;
import org.springframework.web.bind.annotation.RestController;

import java.time.LocalDateTime;
import java.util.List;

@RestController
@RequestMapping("/internal/tasks")
public class InternalTaskController {

    private final TaskApplicationService taskApplicationService;

    public InternalTaskController(TaskApplicationService taskApplicationService) {
        // 这个构造函数的作用是注入任务应用服务，避免 Controller 直接持有仓储实现。
        this.taskApplicationService = taskApplicationService;
    }

    @PostMapping
    public TaskItemResponse createTask(@Valid @RequestBody CreateTaskRequest request) {
        // 这个函数的作用是接收任务创建请求，并把业务处理交给应用服务层。
        return taskApplicationService.create(request);
    }

    @GetMapping
    public List<TaskItemResponse> listTasks(
            @RequestParam(required = false) String chatId,
            @RequestParam(required = false) String senderId,
            @RequestParam(required = false) String sourceEventId,
            @RequestParam(required = false) String status,
            @RequestParam(required = false) String priority,
            @RequestParam(required = false) Boolean todayOnly,
            @RequestParam(required = false) Boolean onlyPending,
            @RequestParam(required = false) @DateTimeFormat(iso = DateTimeFormat.ISO.DATE_TIME) LocalDateTime dueFrom,
            @RequestParam(required = false) @DateTimeFormat(iso = DateTimeFormat.ISO.DATE_TIME) LocalDateTime dueTo,
            @RequestParam(required = false) Integer limit
    ) {
        // 这个函数的作用是暴露任务查询入口，支持 todayOnly、onlyPending、优先级和时间范围筛选。
        return taskApplicationService.list(
                chatId,
                senderId,
                sourceEventId,
                status,
                priority,
                Boolean.TRUE.equals(todayOnly),
                Boolean.TRUE.equals(onlyPending),
                dueFrom,
                dueTo,
                limit
        );
    }
}
