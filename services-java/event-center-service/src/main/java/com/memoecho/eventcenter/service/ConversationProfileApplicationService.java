package com.memoecho.eventcenter.service;

import com.memoecho.eventcenter.dto.ConversationProfileMatchRequest;
import com.memoecho.eventcenter.dto.ConversationProfileMatchResponse;
import com.memoecho.eventcenter.dto.ConversationProfileResponse;
import com.memoecho.eventcenter.dto.ConversationProfileUpsertRequest;
import com.memoecho.eventcenter.model.ConversationProfile;
import com.memoecho.eventcenter.repository.ConversationProfileRepository;
import org.springframework.http.HttpStatus;
import org.springframework.stereotype.Service;
import org.springframework.web.server.ResponseStatusException;

import java.time.Instant;
import java.util.Comparator;
import java.util.List;
import java.util.Locale;
import java.util.Optional;
import java.util.UUID;

@Service
public class ConversationProfileApplicationService {

    private static final String DEFAULT_USER = "default";

    private static final String TRIGGER_ALWAYS = "ALWAYS";
    private static final String TRIGGER_AT_SELF_ONLY = "AT_SELF_ONLY";
    private static final String TRIGGER_KEYWORD_ONLY = "KEYWORD_ONLY";
    private static final String TRIGGER_AT_SELF_OR_KEYWORD = "AT_SELF_OR_KEYWORD";
    private static final String TRIGGER_ADMIN_OR_AT_SELF = "ADMIN_OR_AT_SELF";
    private static final String TRIGGER_MANUAL_ONLY = "MANUAL_ONLY";
    private static final String NOTIFICATION_AUTO = "AUTO";

    private final ConversationProfileRepository repository;

    public ConversationProfileApplicationService(ConversationProfileRepository repository) {
        // 这个构造函数的作用是注入设定集仓储，统一管理设定集的增删改查和命中逻辑。
        this.repository = repository;
    }

    /**
     * 这个函数的作用是返回当前全部设定集，供配置页直接展示和编辑。
     */
    public List<ConversationProfileResponse> listProfiles() {
        return repository.findAll().stream()
                .map(this::toResponse)
                .toList();
    }

    /** 返回当前登录用户自己的会话设定集。 */
    public List<ConversationProfileResponse> listProfiles(String userId) {
        return repository.findAllByUserId(normalizeUserId(userId)).stream().map(this::toResponse).toList();
    }

    /**
     * 这个函数的作用是按 id 读取单个设定集，不存在时返回 404。
     */
    public ConversationProfileResponse getProfile(String profileId) {
        return repository.findById(profileId)
                .map(this::toResponse)
                .orElseThrow(() -> new ResponseStatusException(HttpStatus.NOT_FOUND, "会话设定不存在"));
    }

    /** 在当前用户范围内读取设定，不属于该用户时统一返回不存在。 */
    public ConversationProfileResponse getProfile(String userId, String profileId) {
        return repository.findByIdAndUserId(profileId, normalizeUserId(userId))
                .map(this::toResponse)
                .orElseThrow(() -> new ResponseStatusException(HttpStatus.NOT_FOUND, "会话设定不存在"));
    }

    /**
     * 这个函数的作用是创建新的设定集，并补齐默认值、时间戳和唯一 id。
     */
    public ConversationProfileResponse createProfile(ConversationProfileUpsertRequest request) {
        return createProfile(DEFAULT_USER, request);
    }

    /** 为当前登录用户创建设定，请求体不能改变资源归属。 */
    public ConversationProfileResponse createProfile(String userId, ConversationProfileUpsertRequest request) {
        Instant now = Instant.now();
        ConversationProfile profile = new ConversationProfile(
                UUID.randomUUID().toString(),
                normalizeUserId(userId),
                request.name().trim(),
                normalizeText(request.description()),
                request.enabled() == null || request.enabled(),
                normalizeText(request.platform()),
                normalizeText(request.accountId()),
                normalizeText(request.scene()),
                normalizeText(request.chatType()),
                normalizeIdentifierList(request.chatIds()),
                normalizeIdentifierList(request.targetUserIds()),
                normalizeRoutes(request.supportedRoutes()),
                normalizeTriggerMode(request.triggerMode()),
                normalizeKeywordList(request.triggerKeywords()),
                normalizePersonaMode(request.personaMode()),
                normalizeText(request.systemPrompt()),
                normalizeText(request.skillReference()),
                normalizeSkillReferences(request.skillReference(), request.skillReferences()),
                normalizeText(request.modelProfileId()),
                normalizeText(request.preferredRoute()),
                normalizeReplyMode(request.replyMode()),
                normalizeDelay(request.replyDelaySecondsMin()),
                normalizeDelay(request.replyDelaySecondsMax()),
                normalizeIdentifierList(request.allowedTools()),
                request.requireHumanConfirmation() != null && request.requireHumanConfirmation(),
                normalizePriority(request.priority()),
                now,
                now,
                normalizeNotificationMode(request.notificationMode()),
                normalizeKeywordList(request.notificationKeywords()),
                normalizeDigestWindowSeconds(request.digestWindowSeconds()),
                normalizeDigestMaxMessages(request.digestMaxMessages()),
                request.includeUrgentInDigest() != null && request.includeUrgentInDigest()
        );
        return toResponse(repository.save(profile));
    }

    /**
     * 这个函数的作用是更新已有设定集，并保留原始创建时间。
     */
    public ConversationProfileResponse updateProfile(String profileId, ConversationProfileUpsertRequest request) {
        ConversationProfile existing = repository.findById(profileId)
                .orElseThrow(() -> new ResponseStatusException(HttpStatus.NOT_FOUND, "会话设定不存在"));

        return updateOwnedProfile(existing, request);
    }

    /** 更新当前用户拥有的设定，防止通过猜测 profileId 修改其他用户规则。 */
    public ConversationProfileResponse updateProfile(String userId, String profileId, ConversationProfileUpsertRequest request) {
        ConversationProfile existing = repository.findByIdAndUserId(profileId, normalizeUserId(userId))
                .orElseThrow(() -> new ResponseStatusException(HttpStatus.NOT_FOUND, "会话设定不存在"));

        return updateOwnedProfile(existing, request);
    }

    /** 复用更新对象构造逻辑，并始终保留已有 owner 与创建时间。 */
    private ConversationProfileResponse updateOwnedProfile(ConversationProfile existing, ConversationProfileUpsertRequest request) {

        ConversationProfile updated = new ConversationProfile(
                existing.id(),
                existing.userId(),
                request.name().trim(),
                normalizeText(request.description()),
                request.enabled() == null || request.enabled(),
                normalizeText(request.platform()),
                normalizeText(request.accountId()),
                normalizeText(request.scene()),
                normalizeText(request.chatType()),
                normalizeIdentifierList(request.chatIds()),
                normalizeIdentifierList(request.targetUserIds()),
                normalizeRoutes(request.supportedRoutes()),
                normalizeTriggerMode(request.triggerMode()),
                normalizeKeywordList(request.triggerKeywords()),
                normalizePersonaMode(request.personaMode()),
                normalizeText(request.systemPrompt()),
                normalizeText(request.skillReference()),
                normalizeSkillReferences(request.skillReference(), request.skillReferences()),
                normalizeText(request.modelProfileId()),
                normalizeText(request.preferredRoute()),
                normalizeReplyMode(request.replyMode()),
                normalizeDelay(request.replyDelaySecondsMin()),
                normalizeDelay(request.replyDelaySecondsMax()),
                normalizeIdentifierList(request.allowedTools()),
                request.requireHumanConfirmation() != null && request.requireHumanConfirmation(),
                normalizePriority(request.priority()),
                existing.createdAt(),
                Instant.now(),
                normalizeNotificationMode(request.notificationMode()),
                normalizeKeywordList(request.notificationKeywords()),
                normalizeDigestWindowSeconds(request.digestWindowSeconds()),
                normalizeDigestMaxMessages(request.digestMaxMessages()),
                request.includeUrgentInDigest() != null && request.includeUrgentInDigest()
        );
        return toResponse(repository.save(updated));
    }

    /**
     * 这个函数的作用是删除指定设定集。
     */
    public void deleteProfile(String profileId) {
        repository.deleteById(profileId);
    }

    /** 删除当前用户拥有的设定，其他用户的 id 按不存在处理。 */
    public void deleteProfile(String userId, String profileId) {
        String normalizedUserId = normalizeUserId(userId);
        if (repository.findByIdAndUserId(profileId, normalizedUserId).isEmpty()) {
            throw new ResponseStatusException(HttpStatus.NOT_FOUND, "会话设定不存在");
        }
        repository.deleteByIdAndUserId(profileId, normalizedUserId);
    }

    /**
     * 这个函数的作用是根据账号、会话、对象和 route 等信息匹配最合适的设定集。
     */
    public ConversationProfileMatchResponse matchProfile(ConversationProfileMatchRequest request) {
        return matchProfile(DEFAULT_USER, request);
    }

    /** 只在当前 Runtime 用户的设定集中匹配，避免跨用户套用人格和自动回复策略。 */
    public ConversationProfileMatchResponse matchProfile(String userId, ConversationProfileMatchRequest request) {
        Optional<ConversationProfile> bestProfile = repository.findAllByUserId(normalizeUserId(userId)).stream()
                .filter(ConversationProfile::enabled)
                .filter(profile -> matchesScope(profile, request))
                .max(Comparator
                        .comparingInt(ConversationProfile::priority)
                        .thenComparingInt(this::specificityScore)
                        .thenComparing(ConversationProfile::updatedAt));

        if (bestProfile.isEmpty()) {
            return new ConversationProfileMatchResponse(false, false, "未命中任何会话设定", null);
        }

        ConversationProfile profile = bestProfile.get();
        boolean active = matchesTrigger(profile, request);
        String reason = active ? "命中会话范围且满足触发条件" : "命中会话范围，但当前消息未满足触发条件";
        return new ConversationProfileMatchResponse(true, active, reason, toResponse(profile));
    }

    /** 清理并校验设定集 owner 标识。 */
    private String normalizeUserId(String userId) {
        String normalized = normalizeText(userId);
        if (normalized.isBlank()) {
            throw new ResponseStatusException(HttpStatus.BAD_REQUEST, "用户标识不能为空");
        }
        return normalized;
    }

    /**
     * 这个函数的作用是判断当前消息是否落在设定集的作用域内。
     */
    private boolean matchesScope(ConversationProfile profile, ConversationProfileMatchRequest request) {
        return matchesOptional(profile.platform(), request.platform())
                && matchesOptional(profile.accountId(), request.accountId())
                && matchesOptional(profile.scene(), request.scene())
                && matchesOptional(profile.chatType(), request.chatType())
                && matchesList(profile.chatIds(), request.chatId())
                && matchesList(profile.targetUserIds(), request.senderId())
                && matchesRoute(profile.supportedRoutes(), request.route());
    }

    /**
     * 这个函数的作用是判断当前消息是否满足设定集的触发条件。
     */
    private boolean matchesTrigger(ConversationProfile profile, ConversationProfileMatchRequest request) {
        String triggerMode = normalizeTriggerMode(profile.triggerMode());
        boolean atSelf = request.atSelf() != null && request.atSelf();
        boolean containsKeyword = containsKeyword(request.text(), profile.triggerKeywords());
        boolean adminSender = isAdminSender(request.senderRole());

        return switch (triggerMode) {
            case TRIGGER_AT_SELF_ONLY -> atSelf;
            case TRIGGER_KEYWORD_ONLY -> containsKeyword;
            case TRIGGER_AT_SELF_OR_KEYWORD -> atSelf || containsKeyword;
            case TRIGGER_ADMIN_OR_AT_SELF -> adminSender || atSelf;
            case TRIGGER_MANUAL_ONLY -> false;
            default -> true;
        };
    }

    /**
     * 这个函数的作用是在优先级相同的情况下偏向选择作用域更具体的设定集。
     */
    private int specificityScore(ConversationProfile profile) {
        int score = 0;
        if (!profile.platform().isBlank()) {
            score++;
        }
        if (!profile.accountId().isBlank()) {
            score += 2;
        }
        if (!profile.scene().isBlank()) {
            score++;
        }
        if (!profile.chatType().isBlank()) {
            score++;
        }
        if (!profile.chatIds().isEmpty()) {
            score += 3;
        }
        if (!profile.targetUserIds().isEmpty()) {
            score += 2;
        }
        if (!profile.supportedRoutes().isEmpty()) {
            score += 2;
        }
        if (!profile.triggerKeywords().isEmpty()) {
            score++;
        }
        return score;
    }

    /**
     * 这个函数的作用是把内部设定对象转换成对外返回结构。
     */
    private ConversationProfileResponse toResponse(ConversationProfile profile) {
        return new ConversationProfileResponse(
                profile.id(),
                profile.name(),
                profile.description(),
                profile.enabled(),
                profile.platform(),
                profile.accountId(),
                profile.scene(),
                profile.chatType(),
                profile.chatIds(),
                profile.targetUserIds(),
                profile.supportedRoutes(),
                profile.triggerMode(),
                profile.triggerKeywords(),
                profile.personaMode(),
                profile.systemPrompt(),
                profile.skillReference(),
                profile.skillReferences(),
                profile.modelProfileId(),
                profile.preferredRoute(),
                profile.replyMode(),
                profile.replyDelaySecondsMin(),
                profile.replyDelaySecondsMax(),
                profile.allowedTools(),
                profile.requireHumanConfirmation(),
                profile.priority(),
                profile.createdAt(),
                profile.updatedAt(),
                profile.notificationMode(),
                profile.notificationKeywords(),
                profile.digestWindowSeconds(),
                profile.digestMaxMessages(),
                profile.includeUrgentInDigest()
        );
    }

    /**
     * 这个函数的作用是支持“空值表示通配”的字段匹配规则。
     */
    private boolean matchesOptional(String expected, String actual) {
        return expected == null || expected.isBlank() || expected.equalsIgnoreCase(actual == null ? "" : actual);
    }

    /**
     * 这个函数的作用是支持“空列表表示全部命中”的标识列表匹配规则。
     */
    private boolean matchesList(List<String> expectedValues, String actual) {
        if (expectedValues == null || expectedValues.isEmpty()) {
            return true;
        }
        if (actual == null || actual.isBlank()) {
            return false;
        }
        return expectedValues.stream().anyMatch(value -> value.equalsIgnoreCase(actual));
    }

    /**
     * 这个函数的作用是按 route 判断当前设定是否适用于本次 agent 流程。
     */
    private boolean matchesRoute(List<String> supportedRoutes, String route) {
        if (supportedRoutes == null || supportedRoutes.isEmpty()) {
            return true;
        }
        if (route == null || route.isBlank()) {
            return false;
        }
        String normalizedRoute = route.trim().toLowerCase(Locale.ROOT);
        return supportedRoutes.stream().anyMatch(item -> item.equalsIgnoreCase(normalizedRoute));
    }

    /**
     * 这个函数的作用是检查消息正文中是否命中设定集配置的触发关键词。
     */
    private boolean containsKeyword(String text, List<String> keywords) {
        if (keywords == null || keywords.isEmpty()) {
            return false;
        }
        String normalizedText = text == null ? "" : text.toLowerCase(Locale.ROOT);
        return keywords.stream()
                .filter(keyword -> keyword != null && !keyword.isBlank())
                .map(keyword -> keyword.toLowerCase(Locale.ROOT))
                .anyMatch(normalizedText::contains);
    }

    /**
     * 这个函数的作用是统一识别群管理员和群主身份。
     */
    private boolean isAdminSender(String senderRole) {
        if (senderRole == null || senderRole.isBlank()) {
            return false;
        }
        String normalizedRole = senderRole.toLowerCase(Locale.ROOT);
        return "owner".equals(normalizedRole) || "admin".equals(normalizedRole);
    }

    /**
     * 这个函数的作用是清理 chatId、userId、tool 名等标识列表。
     */
    private List<String> normalizeIdentifierList(List<String> values) {
        if (values == null) {
            return List.of();
        }
        return values.stream()
                .filter(value -> value != null && !value.isBlank())
                .map(String::trim)
                .distinct()
                .toList();
    }

    /**
     * 这个函数的作用是清理关键词列表并保留原始大小写。
     */
    private List<String> normalizeKeywordList(List<String> values) {
        return normalizeIdentifierList(values);
    }

    /**
     * 这个函数的作用是把 route 列表统一成去重后的小写值。
     */
    private List<String> normalizeRoutes(List<String> routes) {
        if (routes == null) {
            return List.of();
        }
        return routes.stream()
                .filter(route -> route != null && !route.isBlank())
                .map(route -> route.trim().toLowerCase(Locale.ROOT))
                .distinct()
                .toList();
    }

    /**
     * 这个函数的作用是合并旧版单 skill 字段和新版 skill 列表字段。
     */
    private List<String> normalizeSkillReferences(String skillReference, List<String> skillReferences) {
        List<String> normalizedList = normalizeIdentifierList(skillReferences);
        String normalizedSingle = normalizeText(skillReference);
        if (normalizedSingle.isBlank()) {
            return normalizedList;
        }
        if (normalizedList.stream().anyMatch(item -> item.equalsIgnoreCase(normalizedSingle))) {
            return normalizedList;
        }
        return java.util.stream.Stream.concat(
                        normalizedList.stream(),
                        java.util.stream.Stream.of(normalizedSingle))
                .distinct()
                .toList();
    }

    /**
     * 这个函数的作用是把可选文本字段统一整理成非 null 字符串。
     */
    private String normalizeText(String value) {
        return value == null ? "" : value.trim();
    }

    /**
     * 这个函数的作用是把触发模式规整为稳定的大写枚举值，缺省时回退到 ALWAYS。
     */
    private String normalizeTriggerMode(String value) {
        String normalized = normalizeText(value).toUpperCase(Locale.ROOT);
        return normalized.isBlank() ? TRIGGER_ALWAYS : normalized;
    }

    /**
     * 这个函数的作用是统一人格模式默认值。
     */
    private String normalizePersonaMode(String value) {
        String normalized = normalizeText(value).toUpperCase(Locale.ROOT);
        return normalized.isBlank() ? "NONE" : normalized;
    }

    /**
     * 这个函数的作用是统一回复模式默认值。
     */
    private String normalizeReplyMode(String value) {
        String normalized = normalizeText(value).toUpperCase(Locale.ROOT);
        return normalized.isBlank() ? "AUTO_REPLY" : normalized;
    }

    /**
     * 这个函数的作用是把负数延迟修正为 0。
     */
    private Integer normalizeDelay(Integer value) {
        if (value == null) {
            return null;
        }
        return Math.max(value, 0);
    }

    /**
     * 这个函数的作用是统一优先级默认值。
     */
    private int normalizePriority(Integer value) {
        return value == null ? 0 : value;
    }

    /**
     * 这个函数的作用是把会话通知模式规范为有限枚举，非法值安全回退到 AUTO。
     */
    private String normalizeNotificationMode(String value) {
        String normalized = normalizeText(value).toUpperCase(Locale.ROOT);
        return switch (normalized) {
            case "URGENT_ONLY", "DIGEST_ONLY", "MUTED" -> normalized;
            default -> NOTIFICATION_AUTO;
        };
    }

    /**
     * 这个函数的作用是限制摘要时间窗口，避免配置过小导致普通消息被频繁打断。
     */
    private Integer normalizeDigestWindowSeconds(Integer value) {
        if (value == null) {
            return null;
        }
        return Math.min(Math.max(value, 60), 86_400);
    }

    /**
     * 这个函数的作用是限制单次归并消息上限，避免单个会话长期积压后生成过大的摘要。
     */
    private Integer normalizeDigestMaxMessages(Integer value) {
        if (value == null) {
            return null;
        }
        return Math.min(Math.max(value, 2), 100);
    }
}
