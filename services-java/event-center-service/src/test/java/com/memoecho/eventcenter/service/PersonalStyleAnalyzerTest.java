package com.memoecho.eventcenter.service;

import com.memoecho.eventcenter.dto.SenderPayload;
import com.memoecho.eventcenter.dto.UnifiedEventPayload;
import com.memoecho.eventcenter.model.StoredEvent;
import org.junit.jupiter.api.Test;

import java.time.Instant;
import java.time.temporal.ChronoUnit;
import java.util.ArrayList;
import java.util.List;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertTrue;

class PersonalStyleAnalyzerTest {

    private final PersonalStyleAnalyzer analyzer = new PersonalStyleAnalyzer();

    @Test
    void shouldOnlyLearnManualMessagesAuthoredByCurrentUser() {
        // 该测试验证对方消息和 Agent 自动发送消息都不会污染个人表达 Skill。
        Instant start = Instant.parse("2026-01-01T00:00:00Z");
        List<StoredEvent> events = new ArrayList<>();
        for (int index = 0; index < 120; index++) {
            events.add(event(
                    "self-" + index,
                    "self",
                    "USER_MANUAL",
                    "收到啦" + index,
                    start.plus(index * 8L, ChronoUnit.HOURS)
            ));
        }
        for (int index = 0; index < 30; index++) {
            events.add(event(
                    "agent-" + index,
                    "self",
                    "AGENT_AUTO",
                    "这是 Agent 自动回复" + index,
                    start.plus(index, ChronoUnit.DAYS)
            ));
            events.add(event(
                    "peer-" + index,
                    "peer",
                    "HISTORY_CONSENTED",
                    "这是对方发送的内容" + index,
                    start.plus(index, ChronoUnit.DAYS)
            ));
        }

        PersonalStyleAnalyzer.PersonalStyleAnalysis result = analyzer.analyze(events, 100, 30);

        assertEquals(150, result.observedOwnMessages());
        assertEquals(120, result.trainableOwnMessages());
        assertEquals(120, result.samples().size());
        assertTrue(result.samples().stream().allMatch(item -> item.eventId().startsWith("self-")));
        assertTrue(result.historySpanDays() >= 30);
        assertTrue(result.confidence() >= 0.75);
        assertEquals(64, result.fingerprint().length());
    }

    @Test
    void shouldLimitDuplicateAndBulkTextSamples() {
        // 该测试验证重复刷屏和整段粘贴内容不会虚增个人 Skill 的成熟度。
        Instant start = Instant.parse("2026-01-01T00:00:00Z");
        List<StoredEvent> events = new ArrayList<>();
        for (int index = 0; index < 20; index++) {
            events.add(event("duplicate-" + index, "self", "USER_MANUAL", "知道了", start.plusSeconds(index)));
        }
        events.add(event("bulk", "self", "USER_MANUAL", "长".repeat(600), start.plusSeconds(30)));

        PersonalStyleAnalyzer.PersonalStyleAnalysis result = analyzer.analyze(events, 100, 30);

        assertEquals(21, result.trainableOwnMessages());
        assertEquals(5, result.samples().size());
        assertEquals(16, result.discardedMessages());
    }

    /**
     * 构造带作者、来源和平台时间的测试事件。
     */
    private StoredEvent event(String id, String senderId, String origin, String text, Instant timestamp) {
        UnifiedEventPayload payload = new UnifiedEventPayload(
                id,
                "qq",
                "life",
                "message",
                "private",
                "chat-1",
                "self",
                new SenderPayload(senderId, senderId, null),
                text,
                List.of(),
                List.of(),
                timestamp.toString(),
                null
        );
        return StoredEvent.received(id, "user-1", payload, timestamp).withMessageOrigin(origin);
    }
}
