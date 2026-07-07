package com.memoecho.schedule.controller;

import com.memoecho.schedule.dto.CreateScheduleRequest;
import com.memoecho.schedule.dto.ScheduleItemResponse;
import com.memoecho.schedule.service.ScheduleApplicationService;
import jakarta.validation.Valid;
import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.PostMapping;
import org.springframework.web.bind.annotation.RequestBody;
import org.springframework.web.bind.annotation.RequestMapping;
import org.springframework.web.bind.annotation.RequestParam;
import org.springframework.web.bind.annotation.RestController;

import java.util.List;

@RestController
@RequestMapping("/internal/schedules")
public class InternalScheduleController {

    private final ScheduleApplicationService scheduleApplicationService;

    public InternalScheduleController(ScheduleApplicationService scheduleApplicationService) {
        this.scheduleApplicationService = scheduleApplicationService;
    }

    @PostMapping
    public ScheduleItemResponse createSchedule(@Valid @RequestBody CreateScheduleRequest request) {
        // Controller 只负责参数接收和校验，真正业务逻辑放在 service。
        return scheduleApplicationService.create(request);
    }

    @GetMapping
    public List<ScheduleItemResponse> listSchedules(
            @RequestParam(required = false) String chatId,
            @RequestParam(required = false) String senderId,
            @RequestParam(required = false) String sourceEventId
    ) {
        // 列表接口先做最小筛选能力，后面再按需要加分页和排序。
        return scheduleApplicationService.list(chatId, senderId, sourceEventId);
    }
}
