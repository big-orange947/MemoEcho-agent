package com.memoecho.schedule.service;

import com.memoecho.schedule.dto.CreateScheduleRequest;
import com.memoecho.schedule.dto.ScheduleItemResponse;
import com.memoecho.schedule.model.ScheduleItem;
import com.memoecho.schedule.repository.ScheduleRepository;
import org.springframework.dao.DuplicateKeyException;
import org.springframework.scheduling.annotation.Scheduled;
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
        try {
            return toResponse(scheduleRepository.save(item));
        } catch (DuplicateKeyException exception) {
            // 并发消费同一事件时，数据库唯一约束只允许一条成功；失败请求回读已有日程即可。
            return scheduleRepository.findBySourceEventId(request.sourceEventId())
                    .map(this::toResponse)
                    .orElseThrow(() -> exception);
        }
    }

    public List<ScheduleItemResponse> list(String chatId, String senderId, String sourceEventId) {
        // 查询前兜底清理一次，保证定时任务尚未执行时客户端也不会看到已过期记录。
        cleanupExpiredSchedules();
        return scheduleRepository.findAll().stream()
                .filter(item -> chatId == null || chatId.isBlank() || item.chatId().equals(chatId))
                .filter(item -> senderId == null || senderId.isBlank() || item.senderId().equals(senderId))
                .filter(item -> sourceEventId == null || sourceEventId.isBlank() || item.sourceEventId().equals(sourceEventId))
                .map(this::toResponse)
                .toList();
    }

    public ScheduleItemResponse findById(String id) {
        // 这个函数的作用是读取单条日程；找不到时由 Controller 返回 404。
        cleanupExpiredSchedules();
        return scheduleRepository.findById(id).map(this::toResponse).orElse(null);
    }

    public boolean delete(String id) {
        // 这个函数的作用是删除用户指定的日程，并让调用方区分成功和不存在。
        return scheduleRepository.deleteById(id);
    }

    public int cleanupExpiredSchedules() {
        // 这个函数的作用是立即清理已结束或日期已过的日程，返回数量便于测试和监控。
        return scheduleRepository.deleteExpired(LocalDateTime.now());
    }

    @Scheduled(fixedDelayString = "${schedule.cleanup.fixed-delay-ms:60000}")
    public void cleanupExpiredSchedulesOnSchedule() {
        // 这个函数是 Spring 定时入口。定时方法保持 void，避免不同 Spring 版本对返回值处理不一致。
        cleanupExpiredSchedules();
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
