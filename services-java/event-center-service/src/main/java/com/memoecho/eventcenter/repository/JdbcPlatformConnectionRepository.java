package com.memoecho.eventcenter.repository;

import com.memoecho.eventcenter.model.PlatformConnection;
import org.springframework.jdbc.core.JdbcTemplate;
import org.springframework.stereotype.Repository;

import java.sql.Timestamp;
import java.time.Instant;
import java.util.List;
import java.util.Optional;

@Repository
public class JdbcPlatformConnectionRepository implements PlatformConnectionRepository {

    private final JdbcTemplate jdbcTemplate;

    public JdbcPlatformConnectionRepository(JdbcTemplate jdbcTemplate) {
        // 这个构造函数的作用是注入 JDBC 访问组件，用于持久化按用户隔离的平台连接档案。
        this.jdbcTemplate = jdbcTemplate;
    }

    @Override
    public PlatformConnection save(PlatformConnection connection) {
        // 这个函数的作用是以更新优先、插入兜底的方式兼容 H2 和 MySQL。
        int updated = jdbcTemplate.update("""
                        UPDATE platform_connection SET name = ?, platform = ?, connector = ?, enabled = ?,
                            connector_base_url = ?, credential_ciphertext = ?, account_id = ?, account_name = ?,
                            health = ?, health_message = ?, last_checked_at = ?, updated_at = ?
                        WHERE id = ? AND user_id = ?
                        """,
                connection.name(), connection.platform(), connection.connector(), connection.enabled(),
                connection.connectorBaseUrl(), connection.credentialCiphertext(), connection.accountId(),
                connection.accountName(), connection.health(), connection.healthMessage(),
                timestamp(connection.lastCheckedAt()), timestamp(connection.updatedAt()),
                connection.id(), connection.userId());
        if (updated == 0) {
            jdbcTemplate.update("""
                            INSERT INTO platform_connection (
                                id, user_id, name, platform, connector, enabled, connector_base_url,
                                credential_ciphertext, account_id, account_name, health, health_message,
                                last_checked_at, created_at, updated_at
                            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                            """,
                    connection.id(), connection.userId(), connection.name(), connection.platform(),
                    connection.connector(), connection.enabled(), connection.connectorBaseUrl(),
                    connection.credentialCiphertext(), connection.accountId(), connection.accountName(),
                    connection.health(), connection.healthMessage(), timestamp(connection.lastCheckedAt()),
                    timestamp(connection.createdAt()), timestamp(connection.updatedAt()));
        }
        return connection;
    }

    @Override
    public List<PlatformConnection> findAllByUserId(String userId) {
        // 这个函数的作用是只读取当前用户拥有的连接，避免跨用户枚举平台账号。
        return jdbcTemplate.query(
                "SELECT * FROM platform_connection WHERE user_id = ? ORDER BY updated_at DESC",
                (rs, rowNum) -> map(rs), userId);
    }

    @Override
    public Optional<PlatformConnection> findByIdAndUserId(String id, String userId) {
        // 这个函数的作用是同时使用连接 ID 与用户 ID 查询，所有修改操作都经过所有权校验。
        return jdbcTemplate.query(
                "SELECT * FROM platform_connection WHERE id = ? AND user_id = ?",
                (rs, rowNum) -> map(rs), id, userId).stream().findFirst();
    }

    @Override
    public void deleteByIdAndUserId(String id, String userId) {
        // 这个函数的作用是只删除当前用户拥有的连接档案。
        jdbcTemplate.update("DELETE FROM platform_connection WHERE id = ? AND user_id = ?", id, userId);
    }

    private PlatformConnection map(java.sql.ResultSet rs) throws java.sql.SQLException {
        // 这个函数的作用是把数据库行还原成连接领域对象，凭据仍保持密文形态。
        return new PlatformConnection(
                rs.getString("id"), rs.getString("user_id"), rs.getString("name"),
                rs.getString("platform"), rs.getString("connector"), rs.getBoolean("enabled"),
                rs.getString("connector_base_url"), rs.getString("credential_ciphertext"),
                rs.getString("account_id"), rs.getString("account_name"), rs.getString("health"),
                rs.getString("health_message"), instant(rs.getTimestamp("last_checked_at")),
                instant(rs.getTimestamp("created_at")), instant(rs.getTimestamp("updated_at")));
    }

    private Timestamp timestamp(Instant value) {
        // 这个函数的作用是把可空 Instant 转为 JDBC Timestamp。
        return value == null ? null : Timestamp.from(value);
    }

    private Instant instant(Timestamp value) {
        // 这个函数的作用是把数据库中的可空 Timestamp 还原为 Instant。
        return value == null ? null : value.toInstant();
    }
}
