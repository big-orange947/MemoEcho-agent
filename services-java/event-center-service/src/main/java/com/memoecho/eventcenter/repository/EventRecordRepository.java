package com.memoecho.eventcenter.repository;

import com.memoecho.eventcenter.model.StoredEvent;

import java.util.List;
import java.util.Optional;

public interface EventRecordRepository {

    boolean exists(String eventId);

    void save(StoredEvent event);

    Optional<StoredEvent> findByEventId(String eventId);

    List<StoredEvent> findAll();
}
