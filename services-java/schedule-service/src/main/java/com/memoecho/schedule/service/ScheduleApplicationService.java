package com.memoecho.schedule.service;

import com.memoecho.schedule.dto.CreateScheduleRequest;
import com.memoecho.schedule.dto.ScheduleItemResponse;
import com.memoecho.schedule.model.ScheduleItem;
import com.memoecho.schedule.repository.ScheduleRepository;
import org.springframework.stereotype.Service;

import java.time.LocalDateTime;
import java.util.List;
import java.util.UUID;

@Service
public class ScheduleApplicationService {

    private final ScheduleRepository scheduleRepository;

    public ScheduleApplicationService(ScheduleRepository scheduleRepository) {
        this.scheduleRepository = scheduleRepository;
    }

    public ScheduleItemResponse create(CreateScheduleRequest request) {
        // 幂等依据是上游消息事件 ID，避免消息重复消费时插入重复日程。
        ScheduleItem existing = scheduleRepository.findBySourceEventId(request.sourceEventId()).orElse(null);
        if (existing != null) {
            return toResponse(existing);
        }

        // 这里把请求 DTO 转成内部存储模型，创建时间由服务端统一生成。
        ScheduleItem item = new ScheduleItem(
                UUID.randomUUID().toString(),
                request.sourceEventId(),
                request.platform(),
                request.chatId(),
                request.senderId(),
                request.title(),
                request.startTime(),
                request.endTime(),
                request.location(),
                request.content(),
                request.participants(),
                request.confidence(),
                LocalDateTime.now()
        );
        return toResponse(scheduleRepository.save(item));
    }

    public List<ScheduleItemResponse> list(String chatId, String senderId, String sourceEventId) {
        // 现在使用的是内存仓库，所以筛选逻辑先保留在 service 层。
        return scheduleRepository.findAll().stream()
                .filter(item -> chatId == null || chatId.isBlank() || item.chatId().equals(chatId))
                .filter(item -> senderId == null || senderId.isBlank() || item.senderId().equals(senderId))
                .filter(item -> sourceEventId == null || sourceEventId.isBlank() || item.sourceEventId().equals(sourceEventId))
                .map(this::toResponse)
                .toList();
    }

    private ScheduleItemResponse toResponse(ScheduleItem item) {
        // 单独保留映射方法，避免 controller 和业务代码直接依赖存储结构。
        return new ScheduleItemResponse(
                item.id(),
                item.sourceEventId(),
                item.platform(),
                item.chatId(),
                item.senderId(),
                item.title(),
                item.startTime(),
                item.endTime(),
                item.location(),
                item.content(),
                item.participants(),
                item.confidence(),
                item.createdAt()
        );
    }
}
