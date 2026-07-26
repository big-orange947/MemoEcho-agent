package com.memoecho.eventcenter.repository;

import com.memoecho.eventcenter.model.AgentDispatchRetryJob;

import java.time.Instant;
import java.util.List;
import java.util.Optional;

public interface AgentDispatchRetryJobRepository {

    /** 新建或更新等待重试的任务。 */
    void schedule(String eventId, int attemptCount, Instant nextAttemptAt, String lastError, Instant now);

    /** 查询已经到达执行时间的重试任务。 */
    List<AgentDispatchRetryJob> findDue(Instant now, int limit);

    /** 原子领取任务，防止多个调度线程重复执行同一个事件。 */
    boolean claim(String eventId, int attemptCount, Instant now);

    /** 标记事件已经由 Runtime 成功处理。 */
    void markSucceeded(String eventId, Instant now);

    /** 标记事件已达到最大次数或遇到不可重试错误。 */
    void markDead(String eventId, int attemptCount, String lastError, Instant now);

    /** 按事件 ID 读取重试状态，供手动重试和测试使用。 */
    Optional<AgentDispatchRetryJob> findByEventId(String eventId);
}
