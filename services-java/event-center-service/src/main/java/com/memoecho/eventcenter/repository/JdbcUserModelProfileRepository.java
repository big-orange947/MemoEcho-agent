package com.memoecho.eventcenter.repository;

import com.memoecho.eventcenter.model.UserModelProfile;
import org.springframework.context.annotation.Primary;
import org.springframework.jdbc.core.JdbcTemplate;
import org.springframework.jdbc.core.RowMapper;
import org.springframework.jdbc.core.ConnectionCallback;
import org.springframework.stereotype.Repository;

import java.sql.ResultSet;
import java.sql.SQLException;
import java.sql.Timestamp;
import java.time.Instant;
import java.util.Arrays;
import java.util.List;
import java.util.Optional;

@Primary
@Repository
public class JdbcUserModelProfileRepository implements UserModelProfileRepository {

    private static final RowMapper<UserModelProfile> ROW_MAPPER = new UserModelProfileRowMapper();

    private final JdbcTemplate jdbcTemplate;
    private final boolean mysqlDatabase;

    public JdbcUserModelProfileRepository(JdbcTemplate jdbcTemplate) {
        // 这个函数的作用是注入 JdbcTemplate，供用户模型配置走数据库持久化读写。
        this.jdbcTemplate = jdbcTemplate;
        this.mysqlDatabase = Boolean.TRUE.equals(jdbcTemplate.execute(
                (ConnectionCallback<Boolean>) connection ->
                        "MySQL".equalsIgnoreCase(connection.getMetaData().getDatabaseProductName())
        ));
    }

    @Override
    public UserModelProfile save(UserModelProfile profile) {
        // MySQL 不支持 H2 的 MERGE；主键冲突时更新全部配置字段，避免编辑操作产生重复记录。
        String sql = mysqlDatabase ? """
                        INSERT INTO user_model_profile (
                            id,
                            user_id,
                            name,
                            description,
                            enabled,
                            provider,
                            base_url,
                            api_key,
                            model,
                            temperature,
                            max_tokens,
                            supported_routes,
                            is_default,
                            priority,
                            created_at,
                            updated_at
                        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                        ON DUPLICATE KEY UPDATE
                            user_id = VALUES(user_id),
                            name = VALUES(name),
                            description = VALUES(description),
                            enabled = VALUES(enabled),
                            provider = VALUES(provider),
                            base_url = VALUES(base_url),
                            api_key = VALUES(api_key),
                            model = VALUES(model),
                            temperature = VALUES(temperature),
                            max_tokens = VALUES(max_tokens),
                            supported_routes = VALUES(supported_routes),
                            is_default = VALUES(is_default),
                            priority = VALUES(priority),
                            created_at = VALUES(created_at),
                            updated_at = VALUES(updated_at)
                        """ : """
                        MERGE INTO user_model_profile (
                            id,
                            user_id,
                            name,
                            description,
                            enabled,
                            provider,
                            base_url,
                            api_key,
                            model,
                            temperature,
                            max_tokens,
                            supported_routes,
                            is_default,
                            priority,
                            created_at,
                            updated_at
                        ) KEY (id) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                        """;
        jdbcTemplate.update(sql,
                profile.id(),
                profile.userId(),
                profile.name(),
                profile.description(),
                profile.enabled(),
                profile.provider(),
                profile.baseUrl(),
                profile.apiKey(),
                profile.model(),
                profile.temperature(),
                profile.maxTokens(),
                joinRoutes(profile.supportedRoutes()),
                profile.isDefault(),
                profile.priority(),
                toTimestamp(profile.createdAt()),
                toTimestamp(profile.updatedAt())
        );
        return profile;
    }

    @Override
    public List<UserModelProfile> findAll() {
        // 这个函数的作用是按更新时间倒序读取全部用户模型配置。
        return jdbcTemplate.query(
                "SELECT * FROM user_model_profile ORDER BY updated_at DESC",
                ROW_MAPPER
        );
    }

    /**
     * 读取指定用户的模型配置，并按最近更新时间倒序返回。
     */
    @Override
    public List<UserModelProfile> findAllByUserId(String userId) {
        return jdbcTemplate.query(
                "SELECT * FROM user_model_profile WHERE user_id = ? ORDER BY updated_at DESC",
                ROW_MAPPER,
                userId
        );
    }

    @Override
    public Optional<UserModelProfile> findById(String profileId) {
        // 这个函数的作用是按 id 查询单条用户模型配置。
        List<UserModelProfile> result = jdbcTemplate.query(
                "SELECT * FROM user_model_profile WHERE id = ?",
                ROW_MAPPER,
                profileId
        );
        return result.stream().findFirst();
    }

    /**
     * 根据配置 id 和所属用户查询记录，作为持久层的所有权约束。
     */
    @Override
    public Optional<UserModelProfile> findByIdAndUserId(String profileId, String userId) {
        List<UserModelProfile> result = jdbcTemplate.query(
                "SELECT * FROM user_model_profile WHERE id = ? AND user_id = ?",
                ROW_MAPPER,
                profileId,
                userId
        );
        return result.stream().findFirst();
    }

    @Override
    public void deleteById(String profileId) {
        // 这个函数的作用是删除指定用户模型配置。
        jdbcTemplate.update("DELETE FROM user_model_profile WHERE id = ?", profileId);
    }

    /**
     * 删除指定用户拥有的模型配置，不匹配用户时不会影响任何记录。
     */
    @Override
    public void deleteByIdAndUserId(String profileId, String userId) {
        jdbcTemplate.update("DELETE FROM user_model_profile WHERE id = ? AND user_id = ?", profileId, userId);
    }

    private static Timestamp toTimestamp(Instant instant) {
        // 这个函数的作用是把 Instant 转成 JDBC 可写入的 Timestamp。
        return Timestamp.from(instant);
    }

    private static String joinRoutes(List<String> routes) {
        // 这个函数的作用是把 route 列表序列化成逗号分隔字符串，便于当前轻量数据库存储。
        if (routes == null || routes.isEmpty()) {
            return "";
        }
        return String.join(",", routes);
    }

    private static List<String> splitRoutes(String routes) {
        // 这个函数的作用是把数据库里的 route 字符串反序列化成列表，空值时返回空列表。
        if (routes == null || routes.isBlank()) {
            return List.of();
        }
        return Arrays.stream(routes.split(","))
                .map(String::trim)
                .filter(item -> !item.isBlank())
                .distinct()
                .toList();
    }

    private static class UserModelProfileRowMapper implements RowMapper<UserModelProfile> {

        @Override
        public UserModelProfile mapRow(ResultSet rs, int rowNum) throws SQLException {
            // 这个函数的作用是把数据库结果集映射成用户模型配置领域对象。
            return new UserModelProfile(
                    rs.getString("id"),
                    rs.getString("user_id"),
                    rs.getString("name"),
                    rs.getString("description"),
                    rs.getBoolean("enabled"),
                    rs.getString("provider"),
                    rs.getString("base_url"),
                    rs.getString("api_key"),
                    rs.getString("model"),
                    rs.getObject("temperature", Double.class),
                    rs.getObject("max_tokens", Integer.class),
                    splitRoutes(rs.getString("supported_routes")),
                    rs.getBoolean("is_default"),
                    rs.getInt("priority"),
                    rs.getTimestamp("created_at").toInstant(),
                    rs.getTimestamp("updated_at").toInstant()
            );
        }
    }
}
