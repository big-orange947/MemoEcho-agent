package com.memoecho.task.repository;

import com.memoecho.task.model.TaskItem;

import java.util.List;
import java.util.Optional;

public interface TaskRepository {

    TaskItem save(TaskItem item);

    List<TaskItem> findAll();

    Optional<TaskItem> findBySourceEventId(String sourceEventId);
}
