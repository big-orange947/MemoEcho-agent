package com.memoecho.schedule.repository;

import com.memoecho.schedule.model.ScheduleItem;

import java.time.LocalDateTime;
import java.util.List;
import java.util.Optional;

public interface ScheduleRepository {

    ScheduleItem save(ScheduleItem item);

    List<ScheduleItem> findAll();

    Optional<ScheduleItem> findBySourceEventId(String sourceEventId);

    Optional<ScheduleItem> findById(String id);

    boolean deleteById(String id);

    int deleteExpired(LocalDateTime referenceTime);
}
