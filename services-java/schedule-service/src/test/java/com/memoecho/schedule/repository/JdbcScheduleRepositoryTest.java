package com.memoecho.schedule.repository;

import com.memoecho.schedule.model.ScheduleItem;
import org.junit.jupiter.api.Test;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.boot.test.autoconfigure.jdbc.JdbcTest;
import org.springframework.context.annotation.Import;
import org.springframework.dao.DuplicateKeyException;

import java.time.LocalDateTime;

import static org.assertj.core.api.Assertions.assertThat;
import static org.assertj.core.api.Assertions.assertThatThrownBy;

@JdbcTest
@Import(JdbcScheduleRepository.class)
class JdbcScheduleRepositoryTest {

    @Autowired
    private JdbcScheduleRepository repository;

    @Test
    void shouldPersistAndReloadCompleteSchedule() {
        // 这个测试验证 JDBC 仓库能完整保存并还原所有日程字段，而不是只在当前进程中保留对象引用。
        LocalDateTime startTime = LocalDateTime.of(2026, 7, 18, 14, 0);
        ScheduleItem source = schedule("persisted", "source-persisted", startTime, startTime.plusHours(2));

        repository.save(source);

        assertThat(repository.findById("persisted"))
                .contains(source);
        assertThat(repository.findBySourceEventId("source-persisted"))
                .contains(source);
    }

    @Test
    void shouldRejectDuplicateSourceEventId() {
        // 这个测试验证数据库唯一约束是真正的最终幂等防线，可覆盖并发请求绕过业务层预查询的情况。
        LocalDateTime startTime = LocalDateTime.of(2026, 7, 18, 14, 0);
        repository.save(schedule("first", "same-source", startTime, null));

        assertThatThrownBy(() -> repository.save(schedule("second", "same-source", startTime, null)))
                .isInstanceOf(DuplicateKeyException.class);
    }

    @Test
    void shouldDeleteSchedulesAfterTheirEffectiveEnd() {
        // 这个测试覆盖两类过期规则：明确结束时间按结束时间，无结束时间按开始时间。
        LocalDateTime referenceTime = LocalDateTime.of(2026, 7, 15, 10, 0);
        repository.save(schedule("ended", "source-ended", referenceTime.minusHours(2), referenceTime.minusMinutes(1)));
        repository.save(schedule("same-day", "source-same-day", referenceTime.minusHours(2), null));
        repository.save(schedule("past-day", "source-past-day", referenceTime.minusDays(1), null));
        repository.save(schedule("future", "source-future", referenceTime.plusHours(2), null));

        assertThat(repository.deleteExpired(referenceTime)).isEqualTo(3);
        assertThat(repository.findById("ended")).isEmpty();
        assertThat(repository.findById("past-day")).isEmpty();
        assertThat(repository.findById("same-day")).isEmpty();
        assertThat(repository.findById("future")).isPresent();
    }

    private ScheduleItem schedule(
            String id,
            String sourceEventId,
            LocalDateTime startTime,
            LocalDateTime endTime
    ) {
        // 这个函数的作用是构造包含中文文本和可空结束时间的测试日程，复用统一的字段基线。
        return new ScheduleItem(
                id,
                sourceEventId,
                "qq",
                "chat-1",
                "sender-1",
                "测试日程",
                startTime,
                endTime,
                "A01-N105",
                "测试日程内容",
                "项目组",
                "high",
                startTime.minusDays(1)
        );
    }
}
