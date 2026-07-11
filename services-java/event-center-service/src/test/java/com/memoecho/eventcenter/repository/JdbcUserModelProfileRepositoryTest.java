package com.memoecho.eventcenter.repository;

import com.memoecho.eventcenter.model.UserModelProfile;
import org.junit.jupiter.api.Test;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.boot.test.autoconfigure.jdbc.JdbcTest;
import org.springframework.context.annotation.Import;
import org.springframework.test.context.TestPropertySource;

import java.time.Instant;
import java.util.List;
import java.util.Optional;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertTrue;

@JdbcTest
@Import(JdbcUserModelProfileRepository.class)
@TestPropertySource(properties = {
        "spring.sql.init.mode=always"
})
class JdbcUserModelProfileRepositoryTest {

    @Autowired
    private JdbcUserModelProfileRepository repository;

    @Test
    void shouldSaveAndLoadProfileFromDatabase() {
        // 这个测试函数的作用是验证数据库仓储能正确保存并读取用户模型配置。
        UserModelProfile profile = new UserModelProfile(
                "model-profile-001",
                "freeze",
                "默认社交模型",
                "用于私聊回复",
                true,
                "OPENAI_COMPATIBLE",
                "https://api.openai.com/v1",
                "sk-demo-001",
                "gpt-4o-mini",
                0.7,
                2048,
                List.of("social_reply", "chat_summary"),
                true,
                8,
                Instant.parse("2026-07-09T00:00:00Z"),
                Instant.parse("2026-07-09T00:10:00Z")
        );

        repository.save(profile);
        Optional<UserModelProfile> reloaded = repository.findById("model-profile-001");

        assertTrue(reloaded.isPresent());
        assertEquals("freeze", reloaded.get().userId());
        assertEquals(List.of("social_reply", "chat_summary"), reloaded.get().supportedRoutes());
        assertEquals("gpt-4o-mini", reloaded.get().model());
    }

    @Test
    void shouldUpdateExistingProfileWithMerge() {
        // 这个测试函数的作用是验证数据库仓储的 save 方法能够覆盖更新已有记录。
        UserModelProfile first = new UserModelProfile(
                "model-profile-002",
                "freeze",
                "任务模型",
                "",
                true,
                "OPENAI_COMPATIBLE",
                "https://example.com/v1",
                "sk-work-001",
                "deepseek-chat",
                0.3,
                4096,
                List.of("task_plan"),
                false,
                10,
                Instant.parse("2026-07-09T01:00:00Z"),
                Instant.parse("2026-07-09T01:00:00Z")
        );
        UserModelProfile second = new UserModelProfile(
                "model-profile-002",
                "freeze",
                "任务模型-更新",
                "新版说明",
                true,
                "OPENAI_COMPATIBLE",
                "https://example.com/v1",
                "sk-work-002",
                "deepseek-v3",
                0.2,
                8192,
                List.of("task_plan", "work_management"),
                true,
                12,
                Instant.parse("2026-07-09T01:00:00Z"),
                Instant.parse("2026-07-09T02:00:00Z")
        );

        repository.save(first);
        repository.save(second);

        UserModelProfile reloaded = repository.findById("model-profile-002").orElseThrow();
        assertEquals("任务模型-更新", reloaded.name());
        assertEquals("deepseek-v3", reloaded.model());
        assertEquals(true, reloaded.isDefault());
        assertEquals(List.of("task_plan", "work_management"), reloaded.supportedRoutes());
    }
}
