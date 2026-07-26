package com.memoecho.eventcenter.service;

import com.memoecho.eventcenter.dto.ConversationSummaryResponse;
import com.memoecho.eventcenter.model.DelegatedTask;
import org.springframework.stereotype.Component;

import java.text.Normalizer;
import java.time.Instant;
import java.util.List;
import java.util.Locale;
import java.util.Optional;
import java.util.UUID;
import java.util.regex.Matcher;
import java.util.regex.Pattern;

/** 把高频自然语言委托转换成可持久化任务草案；低置信输入继续交给原 Agent 路由。 */
@Component
public class DelegatedTaskIntentParser {

    private static final Pattern REPLY_AFTER_VERB = Pattern.compile("(?:帮我|替我)(?:给)?(.{1,20}?)(?:回一下|回复一下|回复|回)(?:消息|一下)?");
    private static final Pattern REPLY_BEFORE_TARGET = Pattern.compile("(?:帮我|替我)(?:回一下|回复一下|回复|回)(.{1,20}?)(?:的消息|消息)");
    private static final Pattern APPOINTMENT_TARGET = Pattern.compile("(?:帮我(?:和|跟)|帮我约|替我(?:和|跟|约))(.{1,20}?)(?:预约|约一下|约|聊|说)");
    private static final Pattern GROUP_TARGET = Pattern.compile("(?:在|到|去)(.{1,20}?)(?:群聊|群里|群中|群内)");
    private static final List<String> PLACEHOLDERS = List.of("某人", "谁谁谁", "一个人", "对方", "他", "她", "ta");

    /**
     * 解析回复或协商类委托，并尝试用已有会话列表绑定唯一联系人。
     * 未找到联系人时仍创建草案，让客户端向用户追问，而不是猜测发送对象。
     */
    public Optional<DelegatedTask> parse(String userId, String command, List<ConversationSummaryResponse> conversations) {
        String normalized = command == null ? "" : command.trim();
        String taskType = detectTaskType(normalized);
        if (taskType == null) {
            return Optional.empty();
        }

        String targetQuery = extractTarget(normalized, taskType);
        String targetChatType = inferTargetChatType(normalized);
        ConversationSummaryResponse target = resolveTarget(targetQuery, targetChatType, conversations);
        boolean targetResolved = target != null;
        String status = targetResolved ? "WAITING_CONFIRMATION" : "WAITING_TARGET";
        String clarification = targetResolved
                ? "请确认是否创建该委托任务。确认后才会进入执行队列。"
                : "你希望我处理哪一个联系人或群聊？请提供昵称、备注、群名或号码。";
        Instant now = Instant.now();

        return Optional.of(new DelegatedTask(
                UUID.randomUUID().toString(), userId, taskType, status, normalized, safe(targetQuery),
                targetResolved ? target.platform() : "", targetResolved ? target.chatType() : "",
                targetResolved ? target.chatId() : "", targetResolved ? target.chatName() : "",
                normalized, buildSuccessCriteria(taskType), extractDeadlineText(normalized),
                targetResolved ? 0.88 : 0.62, clarification, true, now, now
        ));
    }

    /** 根据动词判断是单次回复还是持续协商任务。 */
    private String detectTaskType(String command) {
        String lower = command.toLowerCase(Locale.ROOT);
        if ((lower.contains("帮我") || lower.contains("替我"))
                && (lower.contains("约") || lower.contains("商量") || lower.contains("谈一下"))) {
            return "CONVERSATION_GOAL";
        }
        if ((lower.contains("帮我") || lower.contains("替我"))
                && (lower.contains("回复") || lower.contains("回一下") || lower.contains("回消息"))) {
            return "REPLY_ONCE";
        }
        return null;
    }

    /** 从常见中文表达中提取联系人片段；无法可靠提取时返回空值。 */
    private String extractTarget(String command, String taskType) {
        List<Pattern> patterns = inferTargetChatType(command).equals("group")
                ? List.of(GROUP_TARGET)
                : "CONVERSATION_GOAL".equals(taskType)
                ? List.of(APPOINTMENT_TARGET)
                : List.of(REPLY_BEFORE_TARGET, REPLY_AFTER_VERB);
        for (Pattern pattern : patterns) {
            Matcher matcher = pattern.matcher(command);
            if (matcher.find()) {
                String candidate = cleanTarget(matcher.group(1));
                if (!candidate.isBlank() && !PLACEHOLDERS.contains(candidate.toLowerCase(Locale.ROOT))) {
                    return candidate;
                }
            }
        }
        return "";
    }

    /** 移除自然语言中的虚词，保留可用于会话检索的名称。 */
    private String cleanTarget(String value) {
        return safe(value)
                .replaceFirst("^(给|和)", "")
                .replaceAll("(的|一下)$", "")
                .trim();
    }

    /** 只有唯一名称匹配时才绑定会话，多个同名结果必须继续让用户选择。 */
    private ConversationSummaryResponse resolveTarget(
            String targetQuery,
            String expectedChatType,
            List<ConversationSummaryResponse> conversations
    ) {
        if (targetQuery == null || targetQuery.isBlank() || conversations == null) {
            return null;
        }
        String keyword = normalizeLookupText(targetQuery);
        List<ConversationSummaryResponse> matches = conversations.stream()
                .filter(item -> normalizeChatType(item.chatType()).equals(expectedChatType))
                .filter(item -> normalizeLookupText(item.chatName()).contains(keyword))
                .toList();
        return matches.size() == 1 ? matches.get(0) : null;
    }

    /**
     * 统一联系人检索文本。NFKC 会把 QQ 昵称中的兼容字符“㎞”转换为普通“km”，
     * 但不会修改最终展示和持久化的原始昵称。
     */
    private String normalizeLookupText(String value) {
        return Normalizer.normalize(safe(value), Normalizer.Form.NFKC)
                .trim()
                .toLowerCase(Locale.ROOT);
    }

    /** 只有显式出现群聊措辞时才绑定群；面向具体联系人的委托默认限定为私聊。 */
    private String inferTargetChatType(String command) {
        String normalized = safe(command).toLowerCase(Locale.ROOT);
        return List.of("群聊", "群里", "群中", "群内", "这个群", "群组").stream()
                .anyMatch(normalized::contains) ? "group" : "private";
    }

    /** 统一连接器可能使用的 friend、direct 和 private 等会话类型名称。 */
    private String normalizeChatType(String chatType) {
        String normalized = safe(chatType).toLowerCase(Locale.ROOT);
        if (List.of("private", "friend", "direct", "dm").contains(normalized)) {
            return "private";
        }
        if (List.of("group", "group_chat", "channel").contains(normalized)) {
            return "group";
        }
        return normalized;
    }

    /** 生成可审计的默认成功条件，后续允许用户在确认页修改。 */
    private String buildSuccessCriteria(String taskType) {
        return "CONVERSATION_GOAL".equals(taskType)
                ? "对方明确接受、拒绝或提出需要用户决定的新条件"
                : "生成一条符合当前上下文的回复草稿并由用户确认";
    }

    /** 保留用户原话中的相对截止时间，第一版不擅自换算成绝对时间。 */
    private String extractDeadlineText(String command) {
        for (String keyword : List.of("今天", "明天", "后天", "今晚", "本周", "下周")) {
            if (command.contains(keyword)) {
                return keyword;
            }
        }
        return "";
    }

    /** 把空值统一为安全空字符串。 */
    private String safe(String value) {
        return value == null ? "" : value.trim();
    }
}
