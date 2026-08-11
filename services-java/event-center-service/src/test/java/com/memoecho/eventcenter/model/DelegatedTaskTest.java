package com.memoecho.eventcenter.model;

import org.junit.jupiter.api.Test;

import java.time.Instant;

import static org.assertj.core.api.Assertions.assertThat;

class DelegatedTaskTest {

    /** 验证新任务的可空运行态字段会被规范化为数据库安全的默认值。 */
    @Test
    void shouldNormalizeNullRuntimeFields() {
        Instant now = Instant.parse("2026-08-11T09:00:00Z");

        DelegatedTask task = new DelegatedTask(
                "task-1",
                "user-1",
                "WORKFLOW_STEP",
                "ACTIVE",
                "通知联系人今晚有课",
                "execution-1",
                "联系人",
                "qq",
                "private",
                "10001",
                "联系人",
                "发送通知",
                "通知发送成功",
                "",
                0.95,
                "",
                false,
                "AUTO_COMPLETE",
                null,
                null,
                null,
                now,
                null,
                null,
                now,
                now
        );

        assertThat(task.progressSummary()).isEmpty();
        assertThat(task.stateJson()).isEqualTo("{}");
        assertThat(task.lastEventId()).isEmpty();
        assertThat(task.completionReport()).isEmpty();
    }
}
