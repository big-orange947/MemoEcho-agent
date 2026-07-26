package com.memoecho.eventcenter.repository;

import com.memoecho.eventcenter.model.MemoryCandidate;

import java.time.Instant;
import java.util.List;
import java.util.Optional;

/** 长期记忆候选的持久化边界，所有读取和修改都显式携带 userId。 */
public interface MemoryCandidateRepository {

    /** 新增候选或保存同 ID 的状态变更。 */
    MemoryCandidate save(MemoryCandidate candidate);

    /** 按状态列出当前用户的候选；status 为空时列出全部。 */
    List<MemoryCandidate> findAllByUserIdAndStatus(String userId, String status);

    /** 仅在记录属于当前用户时返回。 */
    Optional<MemoryCandidate> findByIdAndUserId(String id, String userId);

    /** 返回当前用户全部未过期的已确认记忆，具体作用域由应用层匹配。 */
    List<MemoryCandidate> findVerifiedByUserId(String userId, Instant now);

    /** 按事实键和作用域查找仍有效的候选或已确认记录，供 Runtime 幂等合并重复证据。 */
    List<MemoryCandidate> findActiveByFactKey(
            String userId,
            String subject,
            String predicate,
            String scopeType,
            String platform,
            String scene,
            String chatType,
            String chatId
    );

    /** 把已到期的候选或已确认记忆统一标记为 EXPIRED。 */
    int expireDueMemories(String userId, Instant now);

    /** 删除当前用户拥有的指定记忆。 */
    int deleteByIdAndUserId(String id, String userId);
}
