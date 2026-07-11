package com.memoecho.eventcenter.repository;

import com.memoecho.eventcenter.model.LocalUser;
import org.springframework.jdbc.core.JdbcTemplate;
import org.springframework.stereotype.Repository;

import java.sql.Timestamp;
import java.util.Optional;

@Repository
public class JdbcLocalUserRepository implements LocalUserRepository {

    private final JdbcTemplate jdbcTemplate;

    public JdbcLocalUserRepository(JdbcTemplate jdbcTemplate) {
        // 这个构造函数的作用是注入 JDBC 组件，持久化本地登录账户。
        this.jdbcTemplate = jdbcTemplate;
    }

    @Override
    public LocalUser save(LocalUser user) {
        // 这个函数的作用是保存新用户或更新显示名称、密码哈希和启停状态。
        int updated = jdbcTemplate.update("""
                        UPDATE local_user SET username = ?, display_name = ?, password_hash = ?,
                            enabled = ?, updated_at = ? WHERE id = ?
                        """,
                user.username(), user.displayName(), user.passwordHash(), user.enabled(),
                Timestamp.from(user.updatedAt()), user.id());
        if (updated == 0) {
            jdbcTemplate.update("""
                            INSERT INTO local_user (
                                id, username, display_name, password_hash, enabled, created_at, updated_at
                            ) VALUES (?, ?, ?, ?, ?, ?, ?)
                            """,
                    user.id(), user.username(), user.displayName(), user.passwordHash(), user.enabled(),
                    Timestamp.from(user.createdAt()), Timestamp.from(user.updatedAt()));
        }
        return user;
    }

    @Override
    public Optional<LocalUser> findByUsername(String username) {
        // 这个函数的作用是按唯一用户名读取登录账户。
        return jdbcTemplate.query(
                "SELECT * FROM local_user WHERE username = ?",
                (rs, rowNum) -> map(rs), username).stream().findFirst();
    }

    @Override
    public Optional<LocalUser> findById(String id) {
        // 这个函数的作用是按 JWT 中的用户 ID 读取当前账户。
        return jdbcTemplate.query(
                "SELECT * FROM local_user WHERE id = ?",
                (rs, rowNum) -> map(rs), id).stream().findFirst();
    }

    private LocalUser map(java.sql.ResultSet rs) throws java.sql.SQLException {
        // 这个函数的作用是把数据库行还原为本地用户领域对象。
        return new LocalUser(
                rs.getString("id"), rs.getString("username"), rs.getString("display_name"),
                rs.getString("password_hash"), rs.getBoolean("enabled"),
                rs.getTimestamp("created_at").toInstant(), rs.getTimestamp("updated_at").toInstant());
    }
}
