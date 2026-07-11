package com.memoecho.eventcenter.repository;

import com.memoecho.eventcenter.model.ConversationProfile;

import java.util.List;
import java.util.Optional;

public interface ConversationProfileRepository {

    ConversationProfile save(ConversationProfile profile);

    Optional<ConversationProfile> findById(String profileId);

    List<ConversationProfile> findAll();

    void deleteById(String profileId);

    /** 按用户筛选设定集，默认实现便于当前内存仓储与后续 JDBC 仓储共用契约。 */
    default List<ConversationProfile> findAllByUserId(String userId) {
        return findAll().stream().filter(profile -> profile.userId().equals(userId)).toList();
    }

    /** 在用户范围内查询单条设定，避免仅凭 profileId 越权读取。 */
    default Optional<ConversationProfile> findByIdAndUserId(String profileId, String userId) {
        return findById(profileId).filter(profile -> profile.userId().equals(userId));
    }

    /** 仅删除当前用户拥有的设定。 */
    default void deleteByIdAndUserId(String profileId, String userId) {
        if (findByIdAndUserId(profileId, userId).isPresent()) {
            deleteById(profileId);
        }
    }
}
