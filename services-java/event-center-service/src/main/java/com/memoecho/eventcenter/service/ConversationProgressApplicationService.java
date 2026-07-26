package com.memoecho.eventcenter.service;

import com.fasterxml.jackson.databind.JsonNode;
import com.memoecho.eventcenter.dto.ConversationMessageResponse;
import com.memoecho.eventcenter.dto.ConversationProgressResponse;
import org.springframework.stereotype.Service;

import java.time.Instant;
import java.util.Comparator;
import java.util.List;

@Service
public class ConversationProgressApplicationService {

    private final EventCenterApplicationService eventCenterApplicationService;
    private final AgentRuntimeDispatchClient agentRuntimeDispatchClient;

    /** 注入会话查询服务和 Runtime 只读分析客户端。 */
    public ConversationProgressApplicationService(
            EventCenterApplicationService eventCenterApplicationService,
            AgentRuntimeDispatchClient agentRuntimeDispatchClient
    ) {
        this.eventCenterApplicationService = eventCenterApplicationService;
        this.agentRuntimeDispatchClient = agentRuntimeDispatchClient;
    }

    /**
     * 在用户打开上下文时读取一次最新双方消息并生成进度摘要。
     * 结果不持久化，避免后台轮询或普通页面刷新造成频繁模型调用。
     */
    public ConversationProgressResponse buildSnapshot(
            String ownerUserId,
            String platform,
            String chatType,
            String chatId,
            Integer limit,
            String lastSeenAgentEventId
    ) {
        int safeLimit = limit == null || limit <= 0 ? 60 : Math.min(limit, 120);
        List<ConversationMessageResponse> messages = eventCenterApplicationService.findConversationMessages(
                ownerUserId,
                chatId,
                platform,
                chatType,
                safeLimit
        );
        String latestAgentEventId = findLatestAgentEventId(messages);
        boolean summaryUpdated = latestAgentEventId != null
                && !latestAgentEventId.equals(safe(lastSeenAgentEventId));
        JsonNode runtimeResult = summaryUpdated
                ? agentRuntimeDispatchClient.summarizeConversationProgress(
                        ownerUserId,
                        platform,
                        chatType,
                        chatId,
                        messages
                )
                : null;
        String summary = runtimeResult == null ? "" : runtimeResult.path("summary").asText("").trim();
        boolean generatedByModel = runtimeResult != null
                && runtimeResult.path("generatedByModel").asBoolean(false);
        String generatedAt = runtimeResult == null
                ? Instant.now().toString()
                : runtimeResult.path("generatedAt").asText(Instant.now().toString());

        if (summaryUpdated && summary.isBlank()) {
            summary = buildLocalSummary(messages);
            generatedByModel = false;
        } else if (!summaryUpdated && safe(lastSeenAgentEventId).isBlank() && latestAgentEventId == null) {
            // 从未出现 Agent 回复的会话只生成本地概括，不调用模型，也不让首次打开显示空白。
            summary = buildLocalSummary(messages);
        }
        return new ConversationProgressResponse(
                summary,
                generatedByModel,
                generatedAt,
                summaryUpdated,
                latestAgentEventId,
                messages
        );
    }

    /** 查找最近一条真正发送成功的 Agent 消息，作为桌面端判断是否需要重新总结的稳定游标。 */
    private String findLatestAgentEventId(List<ConversationMessageResponse> messages) {
        if (messages == null) {
            return null;
        }
        return messages.stream()
                .filter(this::isAgentMessage)
                .max(Comparator.comparing(message -> safe(message.timestamp())))
                .map(ConversationMessageResponse::eventId)
                .orElse(null);
    }

    /** 只把 Event Center 已确认的 Agent 发送来源计入新回复，人工消息和外部消息不会触发模型总结。 */
    private boolean isAgentMessage(ConversationMessageResponse message) {
        String origin = safe(message.messageOrigin()).toUpperCase();
        return origin.equals("AGENT_AUTO") || origin.equals("AGENT_CONFIRMED");
    }

    /** Runtime 不可用时根据最后一轮双方消息生成自然语言概括，确保弹窗仍然可用。 */
    private String buildLocalSummary(List<ConversationMessageResponse> sourceMessages) {
        if (sourceMessages == null || sourceMessages.isEmpty()) {
            return "当前还没有可用于判断聊天进度的消息记录";
        }
        List<ConversationMessageResponse> messages = sourceMessages.stream()
                .filter(message -> hasReadableContent(message))
                .sorted(Comparator.comparing(message -> safe(message.timestamp())))
                .toList();
        if (messages.isEmpty()) {
            return "当前还没有可用于判断聊天进度的消息记录";
        }

        ConversationMessageResponse latest = messages.get(messages.size() - 1);
        ConversationMessageResponse latestPeer = findLatest(messages, false);
        ConversationMessageResponse latestOwn = findLatest(messages, true);
        boolean waitingForHuman = messages.stream().anyMatch(ConversationMessageResponse::needHumanConfirmation);
        String peerText = shortQuote(latestPeer);
        String ownText = shortQuote(latestOwn);

        if (waitingForHuman) {
            return "对方最近提到" + peerText + "，Agent 已暂停自动回复，当前会话停在等待你确认处理的阶段";
        }
        if (isOwnMessage(latest)) {
            return "对方最近提到" + peerText + "，我方随后回应" + ownText + "，这一轮已经回复完成，目前在等对方继续";
        }
        if (latestOwn != null) {
            return "我方此前回应" + ownText + "，对方最新提到" + peerText + "，当前消息还没有回复，进度停在我方处理阶段";
        }
        return "对方最近提到" + peerText + "，当前还没有我方回复，进度停在等待我方回应的阶段";
    }

    /** 从后向前寻找指定一方最后一条消息。 */
    private ConversationMessageResponse findLatest(List<ConversationMessageResponse> messages, boolean own) {
        for (int index = messages.size() - 1; index >= 0; index--) {
            ConversationMessageResponse message = messages.get(index);
            if (isOwnMessage(message) == own) {
                return message;
            }
        }
        return null;
    }

    /** 只信任 Event Center 已判定的消息来源，避免把群管理员角色误认为当前用户。 */
    private boolean isOwnMessage(ConversationMessageResponse message) {
        String origin = safe(message.messageOrigin()).toUpperCase();
        return origin.equals("USER_MANUAL")
                || origin.equals("AGENT_AUTO")
                || origin.equals("AGENT_CONFIRMED")
                || "self".equalsIgnoreCase(message.senderRole());
    }

    /** 为纯附件消息提供中性占位，不在模型失败时臆测图片或文件内容。 */
    private boolean hasReadableContent(ConversationMessageResponse message) {
        return !safe(message.text()).isBlank()
                || (message.attachments() != null && !message.attachments().isEmpty());
    }

    /** 把最后一条消息压缩成可嵌入摘要的短引用。 */
    private String shortQuote(ConversationMessageResponse message) {
        if (message == null) {
            return "“暂无内容”";
        }
        String text = safe(message.text()).replaceAll("\\s+", " ").trim();
        if (text.isBlank()) {
            int attachmentCount = message.attachments() == null ? 0 : message.attachments().size();
            text = "发送了 " + attachmentCount + " 个附件";
        }
        if (text.length() > 46) {
            text = text.substring(0, 46);
        }
        return "“" + text + "”";
    }

    private String safe(String value) {
        return value == null ? "" : value;
    }
}
