package com.memoecho.task.repository;

import com.memoecho.task.model.TaskItem;
import org.springframework.stereotype.Repository;

import java.util.Comparator;
import java.util.List;
import java.util.Optional;
import java.util.concurrent.ConcurrentHashMap;

@Repository
public class InMemoryTaskRepository implements TaskRepository {

    private final ConcurrentHashMap<String, TaskItem> storage = new ConcurrentHashMap<>();

    @Override
    public TaskItem save(TaskItem item) {
        storage.put(item.id(), item);
        return item;
    }

    @Override
    public List<TaskItem> findAll() {
        return storage.values().stream()
                .sorted(Comparator.comparing(TaskItem::createdAt).reversed())
                .toList();
    }

    @Override
    public Optional<TaskItem> findBySourceEventId(String sourceEventId) {
        return storage.values().stream()
                .filter(item -> item.sourceEventId().equals(sourceEventId))
                .findFirst();
    }
}
