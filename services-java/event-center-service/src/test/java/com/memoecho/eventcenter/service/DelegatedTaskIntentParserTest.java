package com.memoecho.eventcenter.service;

import com.memoecho.eventcenter.dto.ConversationSummaryResponse;
import com.memoecho.eventcenter.model.DelegatedTask;
import org.junit.jupiter.api.Test;

import java.util.List;

import static org.assertj.core.api.Assertions.assertThat;

/** 验证自然语言委托只在意图和目标足够明确时绑定真实会话。 */
class DelegatedTaskIntentParserTest {

    private final DelegatedTaskIntentParser parser = new DelegatedTaskIntentParser();

    /** 普通工作台问题不能被误识别成持续委托。 */
    @Test
    void shouldIgnoreOrdinaryAssistantQuestion() {
        assertThat(parser.parse("freeze", "今天有哪些重要消息", List.of())).isEmpty();
    }

    /** 使用“某人”占位时必须追问目标，不能自行选择联系人。 */
    @Test
    void shouldRequestTargetWhenContactIsPlaceholder() {
        DelegatedTask task = parser.parse("freeze", "帮我回一下某人的消息", List.of()).orElseThrow();

        assertThat(task.taskType()).isEqualTo("REPLY_ONCE");
        assertThat(task.status()).isEqualTo("WAITING_TARGET");
        assertThat(task.chatId()).isBlank();
        assertThat(task.clarificationQuestion()).contains("哪一个联系人或群聊");
    }

    /** 唯一匹配联系人时可以生成待确认任务，但仍不能直接执行发送。 */
    @Test
    void shouldBindUniqueConversationAndPreserveRelativeDeadline() {
        DelegatedTask task = parser.parse(
                "freeze",
                "帮我和小号约一下明天去打球",
                List.of(conversation("3807050597", "小号"))
        ).orElseThrow();

        assertThat(task.taskType()).isEqualTo("CONVERSATION_GOAL");
        assertThat(task.status()).isEqualTo("WAITING_CONFIRMATION");
        assertThat(task.chatId()).isEqualTo("3807050597");
        assertThat(task.targetName()).isEqualTo("小号");
        assertThat(task.deadlineText()).isEqualTo("明天");
        assertThat(task.requiresConfirmation()).isTrue();
    }

    /** 联系人姓名同时存在于群名称时，点对点命令必须绑定私聊。 */
    @Test
    void shouldPreferPrivateContactOverGroupContainingSameName() {
        DelegatedTask task = parser.parse(
                "freeze",
                "帮我和km预约明天下午的课程",
                List.of(
                        conversation("group", "777376261", "哈吉仙、km、freeze"),
                        conversation("private", "3807050597", "km")
                )
        ).orElseThrow();

        assertThat(task.chatType()).isEqualTo("private");
        assertThat(task.chatId()).isEqualTo("3807050597");
        assertThat(task.targetName()).isEqualTo("km");
    }

    /** “预约”必须作为完整动作词被移除，不能把其中的“预”错误拼进联系人名称。 */
    @Test
    void shouldExtractContactBeforeFullAppointmentVerb() {
        DelegatedTask task = parser.parse(
                "freeze",
                "帮我跟km预约一下明天家教的时间，帮我约到晚上七点到九点",
                List.of(conversation("private", "3807050597", "km"))
        ).orElseThrow();

        assertThat(task.targetQuery()).isEqualTo("km");
        assertThat(task.chatId()).isEqualTo("3807050597");
    }

    /** QQ 昵称使用兼容字符“㎞”时，普通键盘输入 km 也必须绑定原始联系人。 */
    @Test
    void shouldNormalizeCompatibilityCharactersWhenMatchingContact() {
        DelegatedTask task = parser.parse(
                "freeze",
                "帮我和km预约一下明天家教的时间，晚上七点到九点",
                List.of(conversation("private", "3807050597", "㎞"))
        ).orElseThrow();

        assertThat(task.status()).isEqualTo("WAITING_CONFIRMATION");
        assertThat(task.chatId()).isEqualTo("3807050597");
        assertThat(task.targetName()).isEqualTo("㎞");
    }

    /** 只有命令显式指定群聊时才允许绑定群会话。 */
    @Test
    void shouldBindGroupOnlyWhenCommandExplicitlyNamesGroup() {
        DelegatedTask task = parser.parse(
                "freeze",
                "帮我在项目组群里约一下明天下午开会",
                List.of(
                        conversation("private", "10001", "项目组"),
                        conversation("group", "20001", "项目组")
                )
        ).orElseThrow();

        assertThat(task.chatType()).isEqualTo("group");
        assertThat(task.chatId()).isEqualTo("20001");
    }

    /** 同名会话不唯一时必须回到目标选择状态。 */
    @Test
    void shouldNotGuessBetweenDuplicateConversationNames() {
        DelegatedTask task = parser.parse(
                "freeze",
                "帮我回复小号消息",
                List.of(conversation("10001", "小号"), conversation("10002", "小号同学"))
        ).orElseThrow();

        assertThat(task.status()).isEqualTo("WAITING_TARGET");
        assertThat(task.chatId()).isBlank();
    }

    /** 构造可参与联系人匹配的会话摘要。 */
    private ConversationSummaryResponse conversation(String chatId, String chatName) {
        return conversation("private", chatId, chatName);
    }

    /** 构造指定会话类型的摘要，用于验证私聊和群聊隔离。 */
    private ConversationSummaryResponse conversation(String chatType, String chatId, String chatName) {
        return new ConversationSummaryResponse(
                "qq", chatType, chatId, chatName, chatName, "你好", "2026-07-20T10:00:00Z",
                "social_reply", "normal", "PROCESSED", "SKIPPED", false,
                0, 0, true, true
        );
    }
}
