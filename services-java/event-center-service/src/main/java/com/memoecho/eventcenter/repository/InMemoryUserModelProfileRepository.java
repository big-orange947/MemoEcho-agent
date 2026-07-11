package com.memoecho.eventcenter.repository;

import com.memoecho.eventcenter.model.UserModelProfile;

import java.util.Comparator;
import java.util.List;
import java.util.Optional;
import java.util.concurrent.ConcurrentHashMap;

public class InMemoryUserModelProfileRepository implements UserModelProfileRepository {

    private final ConcurrentHashMap<String, UserModelProfile> storage = new ConcurrentHashMap<>();

    @Override
    public UserModelProfile save(UserModelProfile profile) {
        // 这个函数的作用是把用户模型配置写入内存仓储，供本地联调和后续服务层复用。
        storage.put(profile.id(), profile);
        return profile;
    }

    @Override
    public List<UserModelProfile> findAll() {
        // 这个函数的作用是返回当前全部用户模型配置，并按更新时间倒序排列，方便前端直接展示最近编辑项。
        return storage.values().stream()
                .sorted(Comparator.comparing(UserModelProfile::updatedAt).reversed())
                .toList();
    }

    /**
     * 返回指定用户的模型配置，行为与 JDBC 实现保持一致。
     */
    @Override
    public List<UserModelProfile> findAllByUserId(String userId) {
        return storage.values().stream()
                .filter(profile -> profile.userId().equals(userId))
                .sorted(Comparator.comparing(UserModelProfile::updatedAt).reversed())
                .toList();
    }

    @Override
    public Optional<UserModelProfile> findById(String profileId) {
        // 这个函数的作用是按 id 查询单条用户模型配置。
        return Optional.ofNullable(storage.get(profileId));
    }

    /**
     * 仅在配置属于指定用户时返回记录，用于内存测试中的权限隔离验证。
     */
    @Override
    public Optional<UserModelProfile> findByIdAndUserId(String profileId, String userId) {
        return findById(profileId).filter(profile -> profile.userId().equals(userId));
    }

    @Override
    public void deleteById(String profileId) {
        // 这个函数的作用是删除指定用户模型配置。
        storage.remove(profileId);
    }

    /**
     * 仅删除指定用户拥有的内存配置，模拟数据库层的所有权限制。
     */
    @Override
    public void deleteByIdAndUserId(String profileId, String userId) {
        storage.computeIfPresent(profileId, (ignored, profile) -> profile.userId().equals(userId) ? null : profile);
    }
}
