package com.memoecho.eventcenter.repository;

import com.memoecho.eventcenter.model.StoredEvent;
import org.springframework.stereotype.Repository;

import java.util.Comparator;
import java.util.List;
import java.util.Optional;
import java.util.concurrent.ConcurrentHashMap;
import java.util.concurrent.ConcurrentMap;

@Repository
public class InMemoryEventRecordRepository implements EventRecordRepository {

    private final ConcurrentMap<String, StoredEvent> records = new ConcurrentHashMap<>();

    @Override
    public boolean exists(String eventId) {
        return records.containsKey(eventId);
    }

    @Override
    public void save(StoredEvent event) {
        records.put(event.eventId(), event);
    }

    @Override
    public Optional<StoredEvent> findByEventId(String eventId) {
        return Optional.ofNullable(records.get(eventId));
    }

    @Override
    public List<StoredEvent> findAll() {
        return records.values().stream()
                .sorted(Comparator.comparing(StoredEvent::receivedAt).reversed())
                .toList();
    }
}
