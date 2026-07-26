package com.memoecho.schedule.controller;

import com.memoecho.schedule.dto.CreateScheduleRequest;
import com.memoecho.schedule.dto.ScheduleItemResponse;
import com.memoecho.schedule.service.ScheduleApplicationService;
import jakarta.validation.Valid;
import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.DeleteMapping;
import org.springframework.web.bind.annotation.PathVariable;
import org.springframework.web.bind.annotation.PostMapping;
import org.springframework.web.bind.annotation.RequestBody;
import org.springframework.web.bind.annotation.RequestMapping;
import org.springframework.web.bind.annotation.RequestParam;
import org.springframework.web.bind.annotation.RestController;
import org.springframework.http.ResponseEntity;

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

    @GetMapping("/{id}")
    public ResponseEntity<ScheduleItemResponse> getSchedule(@PathVariable String id) {
        // 这个函数的作用是给 event-center 提供单条日程详情，便于做权限和来源校验。
        ScheduleItemResponse response = scheduleApplicationService.findById(id);
        return response == null ? ResponseEntity.notFound().build() : ResponseEntity.ok(response);
    }

    @DeleteMapping("/{id}")
    public ResponseEntity<Void> deleteSchedule(@PathVariable String id) {
        // 这个函数的作用是执行真实删除；重复删除返回 404，避免前端误判为成功。
        return scheduleApplicationService.delete(id)
                ? ResponseEntity.noContent().build()
                : ResponseEntity.notFound().build();
    }
}
