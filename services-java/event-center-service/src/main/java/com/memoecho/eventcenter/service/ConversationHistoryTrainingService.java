package com.memoecho.eventcenter.service;

import com.fasterxml.jackson.databind.JsonNode;
import com.memoecho.eventcenter.dto.HistoryTrainingSyncResponse;
import com.memoecho.eventcenter.dto.AttachmentPayload;
import com.memoecho.eventcenter.dto.SenderPayload;
import com.memoecho.eventcenter.dto.UnifiedEventPayload;
import com.memoecho.eventcenter.model.ConversationProfile;
import com.memoecho.eventcenter.model.StoredEvent;
import com.memoecho.eventcenter.repository.ConversationProfileRepository;
import com.memoecho.eventcenter.repository.EventRecordRepository;
import org.springframework.http.HttpStatus;
import org.springframework.stereotype.Service;
import org.springframework.web.server.ResponseStatusException;
import org.springframework.scheduling.annotation.Async;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;

import java.time.Instant;
import java.util.ArrayList;
import java.util.List;

/** 将用户明确授权的 QQ 私聊历史转换为隔离的个人风格训练样本。 */
@Service
public class ConversationHistoryTrainingService {

    private static final Logger log = LoggerFactory.getLogger(ConversationHistoryTrainingService.class);
    private static final int AUTO_CONTEXT_SYNC_LIMIT = 100;

    private final ConversationProfileRepository profileRepository;
    private final EventRecordRepository eventRepository;
    private final QqConnectorMessageClient connectorClient;
    private final PersonalSkillAutoPublisher personalSkillAutoPublisher;

    public ConversationHistoryTrainingService(
            ConversationProfileRepository profileRepository,
            EventRecordRepository eventRepository,
            QqConnectorMessageClient connectorClient,
            PersonalSkillAutoPublisher personalSkillAutoPublisher
    ) {
        this.profileRepository = profileRepository;
        this.eventRepository = eventRepository;
        this.connectorClient = connectorClient;
        this.personalSkillAutoPublisher = personalSkillAutoPublisher;
    }

    /**
     * 同步设定集绑定的私聊历史。历史记录只写入样本库，不派发 Runtime，避免触发回复。
     */
    public HistoryTrainingSyncResponse sync(String userId, String profileId, Integer requestedCount) {
        ConversationProfile profile = profileRepository.findByIdAndUserId(profileId, userId)
                .orElseThrow(() -> new ResponseStatusException(HttpStatus.NOT_FOUND, "会话设定不存在。"));
        if (!profile.historyTrainingEnabled()) {
            throw new ResponseStatusException(HttpStatus.CONFLICT, "该设定尚未授权历史消息用于训练。");
        }
        if (!"qq".equalsIgnoreCase(profile.platform()) || !"private".equalsIgnoreCase(profile.chatType())) {
            throw new ResponseStatusException(HttpStatus.BAD_REQUEST, "目前仅支持同步 QQ 私聊历史。");
        }

        int count = requestedCount == null ? 100 : Math.min(Math.max(requestedCount, 1), 500);
        int imported = 0;
        int duplicates = 0;
        int skipped = 0;
        for (String chatId : profile.chatIds()) {
            JsonNode response = connectorClient.fetchOwnPrivateHistory(chatId, count);
            JsonNode data = response == null ? null : response.path("data");
            JsonNode messages = data == null ? null : data.path("messages");
            String selfId = data == null ? "" : data.path("selfId").asText("");
            if (messages == null || !messages.isArray()) {
                skipped++;
                continue;
            }
            for (JsonNode message : messages) {
                String text = extractText(message);
                String messageId = firstText(message, "message_id", "messageId", "msgId");
                if (messageId.isBlank() || text.isBlank()) {
                    skipped++;
                    continue;
                }
                String eventId = "qq:history-consented:private:" + messageId;
                if (eventRepository.exists(eventId)) {
                    duplicates++;
                    continue;
                }
                UnifiedEventPayload payload = new UnifiedEventPayload(
                        eventId, "qq", "social", "history_import", "private", chatId, selfId,
                        new SenderPayload(selfId, "self", null), text, List.of(), List.of(),
                        resolveTimestamp(message), message, "OWNER", messageId,
                        null, null, resolveSequence(message)
                );
                StoredEvent sample = StoredEvent.received(eventId, userId, payload, Instant.now())
                        .withMessageOrigin("HISTORY_CONSENTED")
                        .markProcessed("TRAINING_SAMPLE", "用户已授权的私聊历史训练样本。", "style_training",
                                "NOT_APPLICABLE", false, Instant.now(), "", null)
                        .markInboxStatus("DONE", null, Instant.now());
                eventRepository.save(sample);
                imported++;
            }
        }
        PersonalSkillAutoPublisher.PublicationResult publication = personalSkillAutoPublisher.evaluate(profile);
        return new HistoryTrainingSyncResponse(
                profileId, profile.chatIds().size(), imported, duplicates, skipped,
                publication.published(), publication.reference(), publication.sampleCount(), publication.confidence()
        );
    }

    /**
     * 保存设定集后自动同步最近私聊记录。
     * 历史上下文授权和训练授权独立：前者保存完整会话，后者才允许历史样本参与个人 Skill 提炼。
     */
    @Async
    public void syncContextAfterProfileSave(ConversationProfile profile) {
        if (!profile.privateHistoryEnabled()) {
            return;
        }
        try {
            HistoryTrainingSyncResponse result = syncConversationContext(
                    profile.userId(), profile, AUTO_CONTEXT_SYNC_LIMIT);
            log.info(
                    "Private history context synced after profile save: profileId={}, imported={}, duplicates={}, skipped={}",
                    profile.id(), result.importedMessages(), result.duplicateMessages(), result.skippedMessages());
        } catch (Exception exception) {
            // 配置已经保存成功；NapCat 暂时不可用时不能回滚用户授权，下一次保存会继续增量补齐。
            log.warn("Private history context sync skipped: profileId={}, error={}", profile.id(), exception.getMessage());
        }
    }

    /** 用户主动刷新时调用，与保存后自动同步使用同一套幂等存档规则。 */
    public HistoryTrainingSyncResponse syncConversationContext(String userId, String profileId, Integer requestedCount) {
        ConversationProfile profile = profileRepository.findByIdAndUserId(profileId, userId)
                .orElseThrow(() -> new ResponseStatusException(HttpStatus.NOT_FOUND, "会话设定不存在"));
        if (!profile.privateHistoryEnabled()) {
            throw new ResponseStatusException(HttpStatus.CONFLICT, "该设定尚未授权读取私聊历史记录");
        }
        return syncConversationContext(userId, profile, requestedCount);
    }

    /**
     * 将 NapCat 返回的完整私聊记录直接写入事件仓库，不派发 Runtime、不创建草稿也不进入收件箱。
     * 使用实时 Webhook 相同的 eventId，因此同一条记录之后实时到达时会自然去重。
     */
    private HistoryTrainingSyncResponse syncConversationContext(
            String userId,
            ConversationProfile profile,
            Integer requestedCount
    ) {
        if (!"qq".equalsIgnoreCase(profile.platform()) || !"private".equalsIgnoreCase(profile.chatType())) {
            throw new ResponseStatusException(HttpStatus.BAD_REQUEST, "目前仅支持同步 QQ 私聊历史");
        }

        int count = requestedCount == null ? AUTO_CONTEXT_SYNC_LIMIT : Math.min(Math.max(requestedCount, 1), 500);
        int imported = 0;
        int duplicates = 0;
        int skipped = 0;
        for (String chatId : profile.chatIds()) {
            JsonNode response = connectorClient.fetchPrivateHistory(chatId, count);
            JsonNode data = response == null ? null : response.path("data");
            JsonNode messages = data == null ? null : data.path("messages");
            String selfId = data == null ? "" : data.path("selfId").asText("");
            if (messages == null || !messages.isArray()) {
                skipped++;
                continue;
            }
            for (JsonNode message : messages) {
                String messageId = firstText(message, "message_id", "messageId", "msgId");
                if (messageId.isBlank()) {
                    skipped++;
                    continue;
                }
                String eventId = "qq:message:private:" + messageId;
                if (eventRepository.exists(eventId)) {
                    duplicates++;
                    continue;
                }

                String senderId = firstText(message.path("sender"), "user_id", "userId");
                if (senderId.isBlank()) {
                    senderId = firstText(message, "user_id", "userId");
                }
                String senderName = firstText(message.path("sender"), "card", "nickname");
                if (senderName.isBlank()) {
                    senderName = senderId.isBlank() ? "unknown" : senderId;
                }
                String text = extractText(message);
                List<AttachmentPayload> attachments = extractAttachments(message);
                if (text.isBlank() && attachments.isEmpty()) {
                    skipped++;
                    continue;
                }

                UnifiedEventPayload payload = new UnifiedEventPayload(
                        eventId, "qq", "social", "history_import", "private", chatId, selfId,
                        new SenderPayload(senderId, senderName, null), text, attachments, List.of(),
                        resolveTimestamp(message), message.deepCopy(),
                        !selfId.isBlank() && selfId.equals(senderId) ? "OWNER" : "CONTACT",
                        messageId, null, null, resolveSequence(message)
                );
                // 完整历史同时包含双方消息。只有 senderId 与当前登录 QQ 一致时，
                // 才能在用户授权后作为本人风格样本；对方消息永远只用于上下文。
                boolean selfAuthored = !selfId.isBlank() && selfId.equals(senderId);
                boolean useForTraining = profile.historyTrainingEnabled() && selfAuthored;
                StoredEvent historyEvent = StoredEvent.received(eventId, userId, payload, Instant.now())
                        .withMessageOrigin(useForTraining ? "HISTORY_CONSENTED" : "HISTORY_CONTEXT")
                        .markProcessed(
                                useForTraining ? "HISTORY_CONTEXT_AND_TRAINING" : "HISTORY_CONTEXT",
                                useForTraining ? "用户授权的私聊历史，已用于上下文和个人风格样本。"
                                        : "用户授权的私聊历史上下文。",
                                "history_context", "NOT_APPLICABLE", false, Instant.now(), "", null
                        )
                        .markInboxStatus("DONE", null, Instant.now());
                eventRepository.save(historyEvent);
                imported++;
            }
        }

        PersonalSkillAutoPublisher.PublicationResult publication = profile.historyTrainingEnabled()
                ? personalSkillAutoPublisher.evaluate(profile)
                : new PersonalSkillAutoPublisher.PublicationResult(false, "", 0, 0.0);
        return new HistoryTrainingSyncResponse(
                profile.id(), profile.chatIds().size(), imported, duplicates, skipped,
                publication.published(), publication.reference(), publication.sampleCount(), publication.confidence());
    }

    /** 将图片、文件、语音和视频统一保存为附件元数据，后续异步媒体任务可继续处理。 */
    private List<AttachmentPayload> extractAttachments(JsonNode message) {
        List<AttachmentPayload> attachments = new ArrayList<>();
        JsonNode segments = message.path("message");
        if (!segments.isArray()) {
            return attachments;
        }
        for (JsonNode segment : segments) {
            String type = segment.path("type").asText("");
            if (!List.of("image", "file", "record", "video").contains(type)) {
                continue;
            }
            JsonNode data = segment.path("data");
            attachments.add(new AttachmentPayload(
                    firstText(data, "file_id", "file"),
                    firstText(data, "file_name", "name"),
                    type,
                    firstText(data, "url", "file")
            ));
        }
        return attachments;
    }

    private String extractText(JsonNode message) {
        String raw = message.path("raw_message").asText("").trim();
        if (!raw.isBlank()) {
            return raw;
        }
        StringBuilder result = new StringBuilder();
        JsonNode segments = message.path("message");
        if (segments.isArray()) {
            for (JsonNode segment : segments) {
                if ("text".equals(segment.path("type").asText())) {
                    result.append(segment.path("data").path("text").asText(""));
                }
            }
        }
        return result.toString().trim();
    }

    private String firstText(JsonNode node, String... names) {
        for (String name : names) {
            String value = node.path(name).asText("");
            if (!value.isBlank()) {
                return value;
            }
        }
        return "";
    }

    private String resolveTimestamp(JsonNode message) {
        long seconds = message.path("time").asLong(0);
        return seconds > 0 ? Instant.ofEpochSecond(seconds).toString() : Instant.now().toString();
    }

    /** 从 NapCat/QCE 历史中提取平台序号，缺失时保留为空并由 eventId 稳定排序。 */
    private Long resolveSequence(JsonNode message) {
        long sequence = message.path("message_seq").asLong(0);
        if (sequence <= 0) {
            sequence = message.path("real_seq").asLong(0);
        }
        return sequence > 0 ? sequence : null;
    }
}
