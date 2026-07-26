package com.memoecho.eventcenter.repository;

import com.memoecho.eventcenter.model.SecureAsset;
import org.springframework.jdbc.core.JdbcTemplate;
import org.springframework.jdbc.core.RowMapper;
import org.springframework.stereotype.Repository;

import java.sql.ResultSet;
import java.sql.SQLException;
import java.sql.Timestamp;
import java.time.Instant;
import java.util.List;
import java.util.Optional;

/**
 * 使用 JdbcTemplate 持久化安全资产，SQL 同时兼容 MySQL 与测试环境 H2。
 */
@Repository
public class JdbcSecureAssetRepository implements SecureAssetRepository {

    private static final RowMapper<SecureAsset> ROW_MAPPER = new SecureAssetRowMapper();
    private final JdbcTemplate jdbcTemplate;

    /** 注入 JdbcTemplate，资产正文在进入本仓储前已经完成加密。 */
    public JdbcSecureAssetRepository(JdbcTemplate jdbcTemplate) {
        this.jdbcTemplate = jdbcTemplate;
    }

    /** 根据资产是否已存在选择 INSERT 或 UPDATE，避免使用 MySQL 不支持的 MERGE。 */
    @Override
    public SecureAsset save(SecureAsset asset) {
        int existing = jdbcTemplate.queryForObject(
                "SELECT COUNT(*) FROM secure_asset WHERE id = ?", Integer.class, asset.id());
        if (existing > 0) {
            jdbcTemplate.update("""
                            UPDATE secure_asset SET
                                user_id = ?, name = ?, type = ?, description = ?, content_type = ?,
                                payload_ciphertext = ?, usage_policy = ?, remaining_uses = ?, enabled = ?,
                                updated_at = ?, last_used_at = ?
                            WHERE id = ?
                            """,
                    asset.userId(), asset.name(), asset.type(), asset.description(), asset.contentType(),
                    asset.payloadCiphertext(), asset.usagePolicy(), asset.remainingUses(), asset.enabled(),
                    toTimestamp(asset.updatedAt()), toNullableTimestamp(asset.lastUsedAt()), asset.id());
        } else {
            jdbcTemplate.update("""
                            INSERT INTO secure_asset (
                                id, user_id, name, type, description, content_type, payload_ciphertext,
                                usage_policy, remaining_uses, enabled, created_at, updated_at, last_used_at
                            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                            """,
                    asset.id(), asset.userId(), asset.name(), asset.type(), asset.description(), asset.contentType(),
                    asset.payloadCiphertext(), asset.usagePolicy(), asset.remainingUses(), asset.enabled(),
                    toTimestamp(asset.createdAt()), toTimestamp(asset.updatedAt()), toNullableTimestamp(asset.lastUsedAt()));
        }
        return asset;
    }

    /** 只列出当前用户资产，确保元数据也不会跨账号泄露。 */
    @Override
    public List<SecureAsset> findAllByUserId(String userId) {
        return jdbcTemplate.query(
                "SELECT * FROM secure_asset WHERE user_id = ? ORDER BY updated_at DESC",
                ROW_MAPPER,
                userId
        );
    }

    /** 使用资产 ID 与用户 ID 双重约束读取记录。 */
    @Override
    public Optional<SecureAsset> findByIdAndUserId(String assetId, String userId) {
        return jdbcTemplate.query(
                "SELECT * FROM secure_asset WHERE id = ? AND user_id = ?",
                ROW_MAPPER,
                assetId,
                userId
        ).stream().findFirst();
    }

    /** 只删除当前用户拥有的资产。 */
    @Override
    public int deleteByIdAndUserId(String assetId, String userId) {
        return jdbcTemplate.update("DELETE FROM secure_asset WHERE id = ? AND user_id = ?", assetId, userId);
    }

    /** 为可重复使用资产记录最近一次解析时间，不改变库存。 */
    @Override
    public int touchReusableAsset(String assetId, String userId, Instant usedAt) {
        return jdbcTemplate.update("""
                        UPDATE secure_asset SET last_used_at = ?, updated_at = ?
                        WHERE id = ? AND user_id = ? AND enabled = TRUE AND usage_policy = 'REUSABLE'
                        """,
                toTimestamp(usedAt), toTimestamp(usedAt), assetId, userId);
    }

    /** 利用 remaining_uses > 0 条件完成原子扣减，阻止并发请求重复消费最后一份库存。 */
    @Override
    public int consumeSingleUseAsset(String assetId, String userId, Instant usedAt) {
        return jdbcTemplate.update("""
                        UPDATE secure_asset
                        SET remaining_uses = remaining_uses - 1, last_used_at = ?, updated_at = ?
                        WHERE id = ? AND user_id = ? AND enabled = TRUE
                          AND usage_policy = 'SINGLE_USE' AND remaining_uses > 0
                        """,
                toTimestamp(usedAt), toTimestamp(usedAt), assetId, userId);
    }

    /** 把 Instant 转换成 JDBC 时间戳。 */
    private static Timestamp toTimestamp(Instant instant) {
        return Timestamp.from(instant);
    }

    /** 允许 lastUsedAt 在资产尚未使用时保存为 null。 */
    private static Timestamp toNullableTimestamp(Instant instant) {
        return instant == null ? null : Timestamp.from(instant);
    }

    /** 将数据库行完整映射成内部资产对象，密文只停留在服务端。 */
    private static final class SecureAssetRowMapper implements RowMapper<SecureAsset> {
        @Override
        public SecureAsset mapRow(ResultSet rs, int rowNum) throws SQLException {
            Timestamp lastUsedAt = rs.getTimestamp("last_used_at");
            return new SecureAsset(
                    rs.getString("id"),
                    rs.getString("user_id"),
                    rs.getString("name"),
                    rs.getString("type"),
                    rs.getString("description"),
                    rs.getString("content_type"),
                    rs.getString("payload_ciphertext"),
                    rs.getString("usage_policy"),
                    rs.getObject("remaining_uses", Integer.class),
                    rs.getBoolean("enabled"),
                    rs.getTimestamp("created_at").toInstant(),
                    rs.getTimestamp("updated_at").toInstant(),
                    lastUsedAt == null ? null : lastUsedAt.toInstant()
            );
        }
    }
}
