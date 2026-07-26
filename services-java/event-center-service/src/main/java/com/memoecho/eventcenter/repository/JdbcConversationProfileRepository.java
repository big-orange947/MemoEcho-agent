package com.memoecho.eventcenter.repository;

import com.fasterxml.jackson.core.JsonProcessingException;
import com.fasterxml.jackson.core.type.TypeReference;
import com.fasterxml.jackson.databind.ObjectMapper;
import com.memoecho.eventcenter.model.ConversationProfile;
import com.memoecho.eventcenter.model.ConversationProfileContext;
import org.springframework.jdbc.core.JdbcTemplate;
import org.springframework.jdbc.core.RowMapper;
import org.springframework.jdbc.core.ConnectionCallback;
import org.springframework.stereotype.Repository;

import java.sql.ResultSet;
import java.sql.SQLException;
import java.sql.Timestamp;
import java.time.Instant;
import java.util.List;
import java.util.Optional;

/**
 * 使用 JDBC 持久化会话设定集，保证应用重启后人格、Skill 和回复策略不会丢失。
 */
@Repository
public class JdbcConversationProfileRepository implements ConversationProfileRepository {

    private static final ObjectMapper OBJECT_MAPPER = new ObjectMapper();
    private static final TypeReference<List<String>> STRING_LIST_TYPE = new TypeReference<>() {
    };
    private static final RowMapper<ConversationProfile> ROW_MAPPER = new ConversationProfileRowMapper();

    private final JdbcTemplate jdbcTemplate;
    private final boolean mysqlDatabase;

    /**
     * 注入 Spring JDBC 模板，用于执行设定集的增删改查。
     */
    public JdbcConversationProfileRepository(JdbcTemplate jdbcTemplate) {
        this.jdbcTemplate = jdbcTemplate;
        this.mysqlDatabase = Boolean.TRUE.equals(jdbcTemplate.execute(
                (ConnectionCallback<Boolean>) connection ->
                        "MySQL".equalsIgnoreCase(connection.getMetaData().getDatabaseProductName())
        ));
    }

    /**
     * 新增或覆盖会话设定。MySQL 不支持 H2 的 MERGE 语法，因此使用主键冲突更新实现幂等保存。
     */
    @Override
    public ConversationProfile save(ConversationProfile profile) {
        String sql = mysqlDatabase ? """
                        INSERT INTO conversation_profile (
                            id, user_id, name, description, enabled, platform, account_id, scene, chat_type,
                            chat_ids_json, target_user_ids_json, supported_routes_json, trigger_mode,
                            trigger_keywords_json, persona_mode, system_prompt, skill_reference,
                            skill_references_json, model_profile_id, preferred_route, reply_mode,
                            reply_delay_seconds_min, reply_delay_seconds_max, allowed_tools_json,
                            require_human_confirmation, priority, created_at, updated_at, notification_mode,
                            notification_keywords_json, digest_window_seconds, digest_max_messages,
                            include_urgent_in_digest, max_reply_chars, split_long_reply,
                            split_reply_chance_percent, private_history_enabled,
                            history_max_messages, history_max_chars, history_training_enabled, review_mode,
                            knowledge_base_sources_json, profile_context_json
                        ) VALUES (
                            ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?,
                            ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?
                        )
                        ON DUPLICATE KEY UPDATE
                            user_id = VALUES(user_id),
                            name = VALUES(name),
                            description = VALUES(description),
                            enabled = VALUES(enabled),
                            platform = VALUES(platform),
                            account_id = VALUES(account_id),
                            scene = VALUES(scene),
                            chat_type = VALUES(chat_type),
                            chat_ids_json = VALUES(chat_ids_json),
                            target_user_ids_json = VALUES(target_user_ids_json),
                            supported_routes_json = VALUES(supported_routes_json),
                            trigger_mode = VALUES(trigger_mode),
                            trigger_keywords_json = VALUES(trigger_keywords_json),
                            persona_mode = VALUES(persona_mode),
                            system_prompt = VALUES(system_prompt),
                            skill_reference = VALUES(skill_reference),
                            skill_references_json = VALUES(skill_references_json),
                            model_profile_id = VALUES(model_profile_id),
                            preferred_route = VALUES(preferred_route),
                            reply_mode = VALUES(reply_mode),
                            reply_delay_seconds_min = VALUES(reply_delay_seconds_min),
                            reply_delay_seconds_max = VALUES(reply_delay_seconds_max),
                            allowed_tools_json = VALUES(allowed_tools_json),
                            require_human_confirmation = VALUES(require_human_confirmation),
                            priority = VALUES(priority),
                            created_at = VALUES(created_at),
                            updated_at = VALUES(updated_at),
                            notification_mode = VALUES(notification_mode),
                            notification_keywords_json = VALUES(notification_keywords_json),
                            digest_window_seconds = VALUES(digest_window_seconds),
                            digest_max_messages = VALUES(digest_max_messages),
                            include_urgent_in_digest = VALUES(include_urgent_in_digest),
                            max_reply_chars = VALUES(max_reply_chars),
                            split_long_reply = VALUES(split_long_reply),
                            split_reply_chance_percent = VALUES(split_reply_chance_percent),
                            private_history_enabled = VALUES(private_history_enabled),
                            history_max_messages = VALUES(history_max_messages),
                            history_max_chars = VALUES(history_max_chars),
                            history_training_enabled = VALUES(history_training_enabled),
                            review_mode = VALUES(review_mode),
                            knowledge_base_sources_json = VALUES(knowledge_base_sources_json),
                            profile_context_json = VALUES(profile_context_json)
                        """ : """
                        MERGE INTO conversation_profile (
                            id, user_id, name, description, enabled, platform, account_id, scene, chat_type,
                            chat_ids_json, target_user_ids_json, supported_routes_json, trigger_mode,
                            trigger_keywords_json, persona_mode, system_prompt, skill_reference,
                            skill_references_json, model_profile_id, preferred_route, reply_mode,
                            reply_delay_seconds_min, reply_delay_seconds_max, allowed_tools_json,
                            require_human_confirmation, priority, created_at, updated_at, notification_mode,
                            notification_keywords_json, digest_window_seconds, digest_max_messages,
                            include_urgent_in_digest, max_reply_chars, split_long_reply,
                            split_reply_chance_percent, private_history_enabled,
                            history_max_messages, history_max_chars, history_training_enabled, review_mode,
                            knowledge_base_sources_json, profile_context_json
                        ) KEY (id) VALUES (
                            ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?,
                            ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?
                        )
                        """;
        jdbcTemplate.update(sql,
                profile.id(),
                profile.userId(),
                profile.name(),
                profile.description(),
                profile.enabled(),
                profile.platform(),
                profile.accountId(),
                profile.scene(),
                profile.chatType(),
                toJson(profile.chatIds()),
                toJson(profile.targetUserIds()),
                toJson(profile.supportedRoutes()),
                profile.triggerMode(),
                toJson(profile.triggerKeywords()),
                profile.personaMode(),
                profile.systemPrompt(),
                profile.skillReference(),
                toJson(profile.skillReferences()),
                profile.modelProfileId(),
                profile.preferredRoute(),
                profile.replyMode(),
                profile.replyDelaySecondsMin(),
                profile.replyDelaySecondsMax(),
                toJson(profile.allowedTools()),
                profile.requireHumanConfirmation(),
                profile.priority(),
                toTimestamp(profile.createdAt()),
                toTimestamp(profile.updatedAt()),
                profile.notificationMode(),
                toJson(profile.notificationKeywords()),
                profile.digestWindowSeconds(),
                profile.digestMaxMessages(),
                profile.includeUrgentInDigest(),
                profile.maxReplyChars(),
                profile.splitLongReply(),
                profile.splitReplyChancePercent(),
                profile.privateHistoryEnabled(),
                profile.historyMaxMessages(),
                profile.historyMaxChars(),
                profile.historyTrainingEnabled(),
                profile.reviewMode(),
                toJson(profile.knowledgeBaseSources()),
                toObjectJson(profile.profileContext())
        );
        return profile;
    }

    /**
     * 按优先级和更新时间读取全部设定集，主要供启动检查和管理任务使用。
     */
    @Override
    public List<ConversationProfile> findAll() {
        return jdbcTemplate.query("""
                SELECT * FROM conversation_profile
                ORDER BY priority DESC, updated_at DESC
                """, ROW_MAPPER);
    }

    /**
     * 只读取指定用户拥有的设定集，防止桌面端用户之间互相看到配置。
     */
    @Override
    public List<ConversationProfile> findAllByUserId(String userId) {
        return jdbcTemplate.query("""
                SELECT * FROM conversation_profile
                WHERE user_id = ?
                ORDER BY priority DESC, updated_at DESC
                """, ROW_MAPPER, userId);
    }

    /**
     * 按主键读取设定集，供内部启动逻辑和兼容代码使用。
     */
    @Override
    public Optional<ConversationProfile> findById(String profileId) {
        return first(jdbcTemplate.query(
                "SELECT * FROM conversation_profile WHERE id = ?",
                ROW_MAPPER,
                profileId
        ));
    }

    /**
     * 在用户所有权范围内读取设定集，是控制器编辑和删除前的安全边界。
     */
    @Override
    public Optional<ConversationProfile> findByIdAndUserId(String profileId, String userId) {
        return first(jdbcTemplate.query(
                "SELECT * FROM conversation_profile WHERE id = ? AND user_id = ?",
                ROW_MAPPER,
                profileId,
                userId
        ));
    }

    /**
     * 按主键删除设定集，保留给内部兼容调用使用。
     */
    @Override
    public void deleteById(String profileId) {
        jdbcTemplate.update("DELETE FROM conversation_profile WHERE id = ?", profileId);
    }

    /**
     * 只有设定属于指定用户时才删除，避免通过猜测 ID 删除他人数据。
     */
    @Override
    public void deleteByIdAndUserId(String profileId, String userId) {
        jdbcTemplate.update(
                "DELETE FROM conversation_profile WHERE id = ? AND user_id = ?",
                profileId,
                userId
        );
    }

    /**
     * 从查询结果中安全取得第一条记录，空结果转换为 Optional.empty()。
     */
    private static Optional<ConversationProfile> first(List<ConversationProfile> profiles) {
        return profiles.stream().findFirst();
    }

    /**
     * 将字符串列表序列化成 JSON，完整保留逗号、URL 和中文提示词内容。
     */
    private static String toJson(List<String> values) {
        try {
            return OBJECT_MAPPER.writeValueAsString(values == null ? List.of() : values);
        } catch (JsonProcessingException exception) {
            throw new IllegalArgumentException("会话设定集列表字段无法序列化", exception);
        }
    }

    /**
     * 将数据库中的 JSON 还原为字符串列表，兼容空列和旧数据空字符串。
     */
    private static List<String> fromJson(String value) {
        if (value == null || value.isBlank()) {
            return List.of();
        }
        try {
            return List.copyOf(OBJECT_MAPPER.readValue(value, STRING_LIST_TYPE));
        } catch (JsonProcessingException exception) {
            throw new IllegalStateException("会话设定集列表字段无法反序列化", exception);
        }
    }

    /** 将结构化会话上下文序列化为 JSON，数据库只增加一个可版本化字段。 */
    private static String toObjectJson(ConversationProfileContext context) {
        try {
            return OBJECT_MAPPER.writeValueAsString(
                    context == null ? ConversationProfileContext.empty() : context
            );
        } catch (JsonProcessingException exception) {
            throw new IllegalArgumentException("结构化会话上下文无法序列化", exception);
        }
    }

    /** 将结构化上下文 JSON 还原为 2.0 模型，空列和旧数据安全回退默认结构。 */
    private static ConversationProfileContext fromContextJson(String value) {
        if (value == null || value.isBlank() || "{}".equals(value.trim())) {
            return ConversationProfileContext.empty();
        }
        try {
            return OBJECT_MAPPER.readValue(value, ConversationProfileContext.class);
        } catch (JsonProcessingException exception) {
            throw new IllegalStateException("结构化会话上下文无法反序列化", exception);
        }
    }

    /**
     * 将领域层时间转换为 JDBC 时间戳。
     */
    private static Timestamp toTimestamp(Instant instant) {
        return Timestamp.from(instant);
    }

    /**
     * 将数据库的一行完整映射为会话设定领域对象。
     */
    private static class ConversationProfileRowMapper implements RowMapper<ConversationProfile> {

        /**
         * 读取标量、JSON 列表和可空延迟字段，重建完整会话设定集。
         */
        @Override
        public ConversationProfile mapRow(ResultSet rs, int rowNum) throws SQLException {
            return new ConversationProfile(
                    rs.getString("id"),
                    rs.getString("user_id"),
                    rs.getString("name"),
                    rs.getString("description"),
                    rs.getBoolean("enabled"),
                    rs.getString("platform"),
                    rs.getString("account_id"),
                    rs.getString("scene"),
                    rs.getString("chat_type"),
                    fromJson(rs.getString("chat_ids_json")),
                    fromJson(rs.getString("target_user_ids_json")),
                    fromJson(rs.getString("supported_routes_json")),
                    rs.getString("trigger_mode"),
                    fromJson(rs.getString("trigger_keywords_json")),
                    rs.getString("persona_mode"),
                    rs.getString("system_prompt"),
                    rs.getString("skill_reference"),
                    fromJson(rs.getString("skill_references_json")),
                    rs.getString("model_profile_id"),
                    rs.getString("preferred_route"),
                    rs.getString("reply_mode"),
                    rs.getObject("reply_delay_seconds_min", Integer.class),
                    rs.getObject("reply_delay_seconds_max", Integer.class),
                    fromJson(rs.getString("allowed_tools_json")),
                    rs.getBoolean("require_human_confirmation"),
                    rs.getInt("priority"),
                    rs.getTimestamp("created_at").toInstant(),
                    rs.getTimestamp("updated_at").toInstant(),
                    rs.getString("notification_mode"),
                    fromJson(rs.getString("notification_keywords_json")),
                    rs.getObject("digest_window_seconds", Integer.class),
                    rs.getObject("digest_max_messages", Integer.class),
                    rs.getBoolean("include_urgent_in_digest"),
                    rs.getObject("max_reply_chars", Integer.class),
                    rs.getObject("split_long_reply", Boolean.class),
                    rs.getObject("split_reply_chance_percent", Integer.class),
                    rs.getBoolean("private_history_enabled"),
                    rs.getObject("history_max_messages", Integer.class),
                    rs.getObject("history_max_chars", Integer.class),
                    rs.getBoolean("history_training_enabled"),
                    rs.getString("review_mode"),
                    fromJson(rs.getString("knowledge_base_sources_json")),
                    fromContextJson(rs.getString("profile_context_json"))
            );
        }
    }
}
