package com.memoecho.eventcenter.repository;

import com.memoecho.eventcenter.model.SecureAsset;

import java.time.Instant;
import java.util.List;
import java.util.Optional;

/**
 * 安全资产的持久化边界，所有查询都要求携带 userId，防止跨用户访问。
 */
public interface SecureAssetRepository {

    /** 保存新资产或覆盖同 ID 的已有资产。 */
    SecureAsset save(SecureAsset asset);

    /** 按更新时间倒序列出指定用户拥有的资产。 */
    List<SecureAsset> findAllByUserId(String userId);

    /** 仅在资产属于指定用户时返回记录。 */
    Optional<SecureAsset> findByIdAndUserId(String assetId, String userId);

    /** 删除指定用户拥有的资产，返回实际删除数量。 */
    int deleteByIdAndUserId(String assetId, String userId);

    /** 更新可重复使用资产的最后使用时间。 */
    int touchReusableAsset(String assetId, String userId, Instant usedAt);

    /** 原子扣减一次性资产库存，库存不足或已停用时返回 0。 */
    int consumeSingleUseAsset(String assetId, String userId, Instant usedAt);
}
