package com.memoecho.eventcenter.repository;

import com.memoecho.eventcenter.model.UserModelProfile;

import java.util.List;
import java.util.Optional;

public interface UserModelProfileRepository {

    UserModelProfile save(UserModelProfile profile);

    List<UserModelProfile> findAll();

    /**
     * 按用户读取模型配置，避免配置中心向其他用户暴露密钥元数据。
     */
    List<UserModelProfile> findAllByUserId(String userId);

    Optional<UserModelProfile> findById(String profileId);

    /**
     * 仅在配置归属当前用户时返回记录，用于服务层完成所有权校验。
     */
    Optional<UserModelProfile> findByIdAndUserId(String profileId, String userId);

    void deleteById(String profileId);

    /**
     * 仅删除指定用户拥有的配置，防止通过猜测 id 删除其他用户记录。
     */
    void deleteByIdAndUserId(String profileId, String userId);
}
