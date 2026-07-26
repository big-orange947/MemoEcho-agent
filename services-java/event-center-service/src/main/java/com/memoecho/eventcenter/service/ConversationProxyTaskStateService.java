package com.memoecho.eventcenter.service;

import com.memoecho.eventcenter.dto.ConversationProxyTaskStateResponse;
import com.memoecho.eventcenter.model.ConversationProfile;
import com.memoecho.eventcenter.model.ConversationProxyTaskState;
import com.memoecho.eventcenter.repository.ConversationProfileRepository;
import com.memoecho.eventcenter.repository.JdbcConversationProxyTaskStateRepository;
import org.springframework.http.HttpStatus;
import org.springframework.stereotype.Service;
import org.springframework.web.server.ResponseStatusException;

import java.nio.charset.StandardCharsets;
import java.security.MessageDigest;
import java.security.NoSuchAlgorithmException;
import java.time.Instant;
import java.util.HexFormat;
import java.util.List;

/** 管理会话任务从进行中、申请结束到用户审批的完整生命周期。 */
@Service
public class ConversationProxyTaskStateService {

    public static final String ACTIVE = "ACTIVE";
    public static final String COMPLETION_REQUESTED = "COMPLETION_REQUESTED";
    public static final String COMPLETED = "COMPLETED";

    private final JdbcConversationProxyTaskStateRepository stateRepository;
    private final ConversationProfileRepository profileRepository;

    /** 注入状态仓储和设定仓储，用于同时校验任务定义与资源归属。 */
    public ConversationProxyTaskStateService(
            JdbcConversationProxyTaskStateRepository stateRepository,
            ConversationProfileRepository profileRepository
    ) {
        this.stateRepository = stateRepository;
        this.profileRepository = profileRepository;
    }

    /** 命中设定时恢复任务状态；目标变化会自动开始一个全新的任务周期。 */
    public ConversationProxyTaskStateResponse resolve(ConversationProfile profile, String chatId) {
        String objective = profile.profileContext().task().objective().trim();
        if (objective.isBlank()) {
            return null;
        }
        String normalizedChatId = normalizeRequired(chatId, "会话标识不能为空");
        String objectiveHash = hashObjective(objective, profile.profileContext().task().successCriteria());
        Instant now = Instant.now();
        ConversationProxyTaskState seed = new ConversationProxyTaskState(
                profile.id(), profile.userId(), profile.platform(), profile.chatType(), normalizedChatId,
                objectiveHash, ACTIVE, "", "", List.of(), null, null, now, now
        );
        ConversationProxyTaskState state = stateRepository.find(profile.id(), normalizedChatId)
                .map(existing -> existing.objectiveHash().equals(objectiveHash) ? existing : stateRepository.reset(seed))
                .orElseGet(() -> stateRepository.insert(seed));
        return toResponse(state, profile.name());
    }

    /** 接收 Runtime 的完成判断，必须属于当前用户且已有可运行任务状态。 */
    public ConversationProxyTaskStateResponse requestCompletion(
            String userId, String profileId, String chatId, String summary, String reason, List<String> evidence
    ) {
        ConversationProfile profile = requireOwnedProfile(userId, profileId);
        ConversationProxyTaskStateResponse current = resolve(profile, chatId);
        if (current == null) {
            throw new ResponseStatusException(HttpStatus.CONFLICT, "当前设定没有会话任务");
        }
        if (COMPLETED.equals(current.status())) {
            return current;
        }
        ConversationProxyTaskState updated = stateRepository.requestCompletion(
                profileId, chatId.trim(), normalize(summary), normalize(reason), normalizeEvidence(evidence)
        );
        return toResponse(updated, profile.name());
    }

    /** 用户批准结束或驳回申请；未处于等待审批状态时拒绝修改。 */
    public ConversationProxyTaskStateResponse decide(
            String userId, String profileId, String chatId, boolean approved
    ) {
        ConversationProfile profile = requireOwnedProfile(userId, profileId);
        ConversationProxyTaskState existing = stateRepository.find(profileId, normalizeRequired(chatId, "会话标识不能为空"))
                .orElseThrow(() -> new ResponseStatusException(HttpStatus.NOT_FOUND, "会话任务状态不存在"));
        if (!COMPLETION_REQUESTED.equals(existing.status())) {
            throw new ResponseStatusException(HttpStatus.CONFLICT, "当前任务没有待审批的结束申请");
        }
        return toResponse(stateRepository.decide(profileId, chatId.trim(), approved), profile.name());
    }

    /** 返回用户所有等待处理的结束申请，供客户端显示审批入口。 */
    public List<ConversationProxyTaskStateResponse> listPending(String userId) {
        return stateRepository.findPendingByUserId(userId).stream()
                .map(state -> toResponse(
                        state,
                        profileRepository.findByIdAndUserId(state.profileId(), userId)
                                .map(ConversationProfile::name).orElse("会话任务")
                ))
                .toList();
    }

    /** 校验设定属于当前用户，避免跨账号审批任务。 */
    private ConversationProfile requireOwnedProfile(String userId, String profileId) {
        return profileRepository.findByIdAndUserId(profileId, userId)
                .orElseThrow(() -> new ResponseStatusException(HttpStatus.NOT_FOUND, "会话设定不存在"));
    }

    /** 对任务目标和成功条件生成稳定指纹，用户修改任一内容都会触发状态重置。 */
    private String hashObjective(String objective, List<String> successCriteria) {
        try {
            MessageDigest digest = MessageDigest.getInstance("SHA-256");
            String value = objective.trim() + "\n" + String.join("\n", successCriteria == null ? List.of() : successCriteria);
            return HexFormat.of().formatHex(digest.digest(value.getBytes(StandardCharsets.UTF_8)));
        } catch (NoSuchAlgorithmException exception) {
            throw new IllegalStateException("当前 JDK 不支持 SHA-256", exception);
        }
    }

    /** 清理模型提交的证据列表，限制数量和长度，避免异常响应污染数据库。 */
    private List<String> normalizeEvidence(List<String> evidence) {
        if (evidence == null) {
            return List.of();
        }
        return evidence.stream().map(this::normalize).filter(value -> !value.isBlank()).limit(10).toList();
    }

    /** 清理可空文本。 */
    private String normalize(String value) {
        return value == null ? "" : value.trim();
    }

    /** 清理必填文本并返回明确的 400。 */
    private String normalizeRequired(String value, String message) {
        String normalized = normalize(value);
        if (normalized.isBlank()) {
            throw new ResponseStatusException(HttpStatus.BAD_REQUEST, message);
        }
        return normalized;
    }

    /** 将内部状态转换为客户端和 Runtime 共用响应。 */
    private ConversationProxyTaskStateResponse toResponse(ConversationProxyTaskState state, String profileName) {
        return new ConversationProxyTaskStateResponse(
                state.profileId(), profileName, state.platform(), state.chatType(), state.chatId(), state.status(),
                state.completionSummary(), state.completionReason(), state.completionEvidence(), state.requestedAt(),
                state.decidedAt(), state.updatedAt()
        );
    }
}
