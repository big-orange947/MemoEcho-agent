package com.memoecho.schedule.repository;

import com.memoecho.schedule.model.ScheduleItem;
import org.springframework.stereotype.Repository;

import java.util.Comparator;
import java.util.List;
import java.util.Optional;
import java.util.concurrent.ConcurrentHashMap;

@Repository
public class InMemoryScheduleRepository implements ScheduleRepository {

    private final ConcurrentHashMap<String, ScheduleItem> storage = new ConcurrentHashMap<>();

    @Override
    public ScheduleItem save(ScheduleItem item) {
        storage.put(item.id(), item);
        return item;
    }

    @Override
    public List<ScheduleItem> findAll() {
        return storage.values().stream()
                .sorted(Comparator.comparing(ScheduleItem::startTime).thenComparing(ScheduleItem::createdAt))
                .toList();
    }

    @Override
    public Optional<ScheduleItem> findBySourceEventId(String sourceEventId) {
        return storage.values().stream()
                .filter(item -> item.sourceEventId().equals(sourceEventId))
                .findFirst();
    }
}

