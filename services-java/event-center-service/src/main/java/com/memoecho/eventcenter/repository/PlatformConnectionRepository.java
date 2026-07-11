package com.memoecho.eventcenter.repository;

import com.memoecho.eventcenter.model.PlatformConnection;

import java.util.List;
import java.util.Optional;

public interface PlatformConnectionRepository {

    /** 保存或更新连接档案。 */
    PlatformConnection save(PlatformConnection connection);

    /** 查询指定用户的全部连接档案。 */
    List<PlatformConnection> findAllByUserId(String userId);

    /** 按连接 ID 和用户 ID 查询，用于强制执行所有权边界。 */
    Optional<PlatformConnection> findByIdAndUserId(String id, String userId);

    /** 只删除指定用户拥有的连接档案。 */
    void deleteByIdAndUserId(String id, String userId);
}
