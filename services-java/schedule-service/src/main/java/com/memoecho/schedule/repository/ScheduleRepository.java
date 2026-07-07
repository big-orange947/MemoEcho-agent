package com.memoecho.schedule.repository;

import com.memoecho.schedule.model.ScheduleItem;

import java.util.List;
import java.util.Optional;

public interface ScheduleRepository {

    ScheduleItem save(ScheduleItem item);

    List<ScheduleItem> findAll();

    Optional<ScheduleItem> findBySourceEventId(String sourceEventId);
}

