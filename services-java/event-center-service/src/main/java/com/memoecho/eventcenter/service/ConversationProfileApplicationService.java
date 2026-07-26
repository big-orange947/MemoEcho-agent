package com.memoecho.eventcenter.service;

import com.memoecho.eventcenter.dto.ConversationProfileMatchRequest;
import com.memoecho.eventcenter.dto.ConversationProfileMatchResponse;
import com.memoecho.eventcenter.dto.ConversationProfileResponse;
import com.memoecho.eventcenter.dto.ConversationProfileUpsertRequest;
import com.memoecho.eventcenter.model.ConversationProfile;
import com.memoecho.eventcenter.model.ConversationProfileContext;
import com.memoecho.eventcenter.repository.ConversationProfileRepository;
import org.springframework.http.HttpStatus;
import org.springframework.beans.factory.annotation.Autowired;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
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

    private static final Logger log = LoggerFactory.getLogger(ConversationProfileApplicationService.class);

    private static final String DEFAULT_USER = "default";

    private static final String TRIGGER_ALWAYS = "ALWAYS";
    private static final String TRIGGER_AT_SELF_ONLY = "AT_SELF_ONLY";
    private static final String TRIGGER_KEYWORD_ONLY = "KEYWORD_ONLY";
    private static final String TRIGGER_AT_SELF_OR_KEYWORD = "AT_SELF_OR_KEYWORD";
    private static final String TRIGGER_ADMIN_OR_AT_SELF = "ADMIN_OR_AT_SELF";
    private static final String TRIGGER_MANUAL_ONLY = "MANUAL_ONLY";
    private static final String NOTIFICATION_AUTO = "AUTO";

    private final ConversationProfileRepository repository;
    private ConversationHistoryTrainingService historyTrainingService;
    private ConversationProxyTaskStateService taskStateService;

    public ConversationProfileApplicationService(ConversationProfileRepository repository) {
        // 这个构造函数的作用是注入设定集仓储，统一管理设定集的增删改查和命中逻辑。
        this.repository = repository;
    }

    /**
     * 仅在完整应用上下文中注入历史同步服务；纯设定集单元测试无需启动 QQ Connector。
     */
    @Autowired(required = false)
    public void setHistoryTrainingService(ConversationHistoryTrainingService historyTrainingService) {
        this.historyTrainingService = historyTrainingService;
    }

    /** 完整应用中注入任务状态服务；纯内存单元测试继续兼容无数据库状态模式。 */
    @Autowired(required = false)
    public void setTaskStateService(ConversationProxyTaskStateService taskStateService) {
        this.taskStateService = taskStateService;
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
                request.includeUrgentInDigest() != null && request.includeUrgentInDigest(),
                normalizeMaxReplyChars(request.maxReplyChars()),
                request.splitLongReply() == null || request.splitLongReply(),
                normalizeSplitReplyChancePercent(request.splitReplyChancePercent()),
                request.privateHistoryEnabled() != null && request.privateHistoryEnabled(),
                normalizeHistoryMaxMessages(request.historyMaxMessages()),
                normalizeHistoryMaxChars(request.historyMaxChars()),
                request.historyTrainingEnabled() != null && request.historyTrainingEnabled(),
                normalizeReviewMode(request.reviewMode()),
                normalizeKnowledgeBaseSources(request.knowledgeBaseSources()),
                normalizeProfileContext(request.profileContext())
        );
        ConversationProfile savedProfile = repository.save(profile);
        syncPrivateHistoryAfterSave(savedProfile);
        return toResponse(savedProfile);
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
                request.includeUrgentInDigest() != null && request.includeUrgentInDigest(),
                normalizeMaxReplyChars(request.maxReplyChars()),
                request.splitLongReply() == null || request.splitLongReply(),
                normalizeSplitReplyChancePercent(request.splitReplyChancePercent()),
                request.privateHistoryEnabled() != null && request.privateHistoryEnabled(),
                normalizeHistoryMaxMessages(request.historyMaxMessages()),
                normalizeHistoryMaxChars(request.historyMaxChars()),
                request.historyTrainingEnabled() != null && request.historyTrainingEnabled(),
                normalizeReviewMode(request.reviewMode()),
                normalizeKnowledgeBaseSources(request.knowledgeBaseSources()),
                normalizeProfileContext(request.profileContext())
        );
        ConversationProfile savedProfile = repository.save(updated);
        syncPrivateHistoryAfterSave(savedProfile);
        return toResponse(savedProfile);
    }

    /** 保存后立即同步授权私聊的最新记录；Connector 暂不可用不能导致设定保存失败。 */
    private void syncPrivateHistoryAfterSave(ConversationProfile profile) {
        if (historyTrainingService == null || !profile.privateHistoryEnabled()) {
            return;
        }
        try {
            historyTrainingService.syncContextAfterProfileSave(profile);
        } catch (Exception exception) {
            // 同步服务本身已降级处理，这一层额外兜底防止未来实现变更影响配置保存。
            log.warn("设定集已保存，但历史同步未完成：profileId={}, error={}", profile.id(), exception.getMessage());
        }
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
     * 按用户选择暂停当前会话的自动代理；系统消息监控规则不受影响。
     * continueAgent=true 时不修改原设定，避免错误覆盖原本的草稿或静默策略。
     */
    public int updateConversationAgentState(String userId, String platform, String chatType, String chatId, boolean continueAgent) {
        if (continueAgent) {
            return 0;
        }
        List<ConversationProfile> matched = repository.findAllByUserId(normalizeUserId(userId)).stream()
                .filter(ConversationProfile::enabled)
                .filter(profile -> !"__MESSAGE_MONITORING__".equals(profile.description()))
                .filter(profile -> matchesOptional(profile.platform(), platform))
                .filter(profile -> matchesOptional(profile.chatType(), chatType))
                .filter(profile -> matchesList(profile.chatIds(), chatId))
                .toList();
        matched.forEach(profile -> repository.save(copyWithEnabled(profile, false)));
        return matched.size();
    }

    /** 复制完整设定，仅修改 enabled，避免暂停操作丢失人格、Skill 或摘要参数。 */
    private ConversationProfile copyWithEnabled(ConversationProfile profile, boolean enabled) {
        return new ConversationProfile(
                profile.id(), profile.userId(), profile.name(), profile.description(), enabled,
                profile.platform(), profile.accountId(), profile.scene(), profile.chatType(), profile.chatIds(),
                profile.targetUserIds(), profile.supportedRoutes(), profile.triggerMode(), profile.triggerKeywords(),
                profile.personaMode(), profile.systemPrompt(), profile.skillReference(), profile.skillReferences(),
                profile.modelProfileId(), profile.preferredRoute(), profile.replyMode(), profile.replyDelaySecondsMin(),
                profile.replyDelaySecondsMax(), profile.allowedTools(), profile.requireHumanConfirmation(),
                profile.priority(), profile.createdAt(), Instant.now(), profile.notificationMode(),
                profile.notificationKeywords(), profile.digestWindowSeconds(), profile.digestMaxMessages(),
                profile.includeUrgentInDigest(), profile.maxReplyChars(), profile.splitLongReply(),
                profile.splitReplyChancePercent(), profile.privateHistoryEnabled(), profile.historyMaxMessages(),
                profile.historyMaxChars(), profile.historyTrainingEnabled(), profile.reviewMode(),
                profile.knowledgeBaseSources(), profile.profileContext()
        );
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
        var taskState = taskStateService == null ? null : taskStateService.resolve(profile, request.chatId());
        if (taskState != null && ConversationProxyTaskStateService.COMPLETED.equals(taskState.status())) {
            return new ConversationProfileMatchResponse(
                    true, false, "会话任务已经由用户批准结束，当前代理已停止", toResponse(profile), taskState
            );
        }
        String reason = active ? "命中会话范围且满足触发条件" : "命中会话范围，但当前消息未满足触发条件";
        if (taskState != null && ConversationProxyTaskStateService.COMPLETION_REQUESTED.equals(taskState.status())) {
            reason = "任务已申请结束并等待用户审批，审批前继续代理但不得重复已完成目标";
        }
        return new ConversationProfileMatchResponse(true, active, reason, toResponse(profile), taskState);
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
                profile.includeUrgentInDigest(),
                profile.maxReplyChars(),
                profile.splitLongReply(),
                profile.splitReplyChancePercent(),
                profile.privateHistoryEnabled(),
                profile.historyMaxMessages(),
                profile.historyMaxChars(),
                profile.historyTrainingEnabled(),
                profile.reviewMode(),
                profile.knowledgeBaseSources(),
                profile.profileContext()
          );
      }

    /**
     * 清理 Conversation Profile 2.0 的结构化上下文。
     * 该层只处理长度、空值和枚举，不把业务目标转换成工具权限。
     */
    private ConversationProfileContext normalizeProfileContext(ConversationProfileContext context) {
        if (context == null) {
            return ConversationProfileContext.empty();
        }
        ConversationProfileContext.Identity identity = context.identity() == null
                ? ConversationProfileContext.Identity.empty()
                : context.identity();
        ConversationProfileContext.Counterparty counterparty = context.counterparty() == null
                ? ConversationProfileContext.Counterparty.empty()
                : context.counterparty();
        ConversationProfileContext.Background background = context.background() == null
                ? ConversationProfileContext.Background.empty()
                : context.background();
        ConversationProfileContext.Task task = context.task() == null
                ? ConversationProfileContext.Task.empty()
                : context.task();
        ConversationProfileContext.BusinessRules businessRules = context.businessRules() == null
                ? ConversationProfileContext.BusinessRules.empty()
                : context.businessRules();
        ConversationProfileContext.MemoryPolicy memoryPolicy = context.memoryPolicy() == null
                ? ConversationProfileContext.MemoryPolicy.empty()
                : context.memoryPolicy();

        return new ConversationProfileContext(
                2,
                new ConversationProfileContext.Identity(
                        defaultContextText(identity.representedPerson(), "本人", 500),
                        defaultContextText(identity.role(), "本人", 1000),
                        normalizeContextText(identity.speakingStyle(), 2000),
                        normalizeContextList(identity.forbiddenExpressions(), 30, 200)
                ),
                new ConversationProfileContext.Counterparty(
                        normalizeContextText(counterparty.name(), 255),
                        normalizeContextText(counterparty.identity(), 1000),
                        normalizeContextText(counterparty.relationship(), 500),
                        normalizeContextText(counterparty.preferredAddress(), 100),
                        normalizeContextList(counterparty.knownFacts(), 50, 500),
                        normalizeTrustLevel(counterparty.trustLevel()),
                        normalizeContextText(counterparty.communicationPreference(), 1000)
                ),
                new ConversationProfileContext.Background(
                        normalizeContextText(background.origin(), 2000),
                        normalizeContextText(background.previousEvents(), 4000),
                        normalizeContextText(background.currentProgress(), 2000)
                ),
                new ConversationProfileContext.Task(
                        normalizeContextText(task.objective(), 2000),
                        normalizeContextList(task.successCriteria(), 30, 500),
                        normalizeContextText(task.deadline(), 255),
                        normalizeContextList(task.prohibitedActions(), 30, 500)
                ),
                new ConversationProfileContext.BusinessRules(
                        normalizeContextText(businessRules.pricingPolicy(), 2000),
                        normalizeContextText(businessRules.minimumPrice(), 500),
                        normalizeContextText(businessRules.refundPolicy(), 2000),
                        normalizeContextText(businessRules.deliveryConditions(), 2000),
                        normalizeContextList(businessRules.hardConstraints(), 30, 500)
                ),
                new ConversationProfileContext.MemoryPolicy(memoryPolicy.extractionEnabled()),
                normalizeAssetReferences(context.assets())
        );
    }

    /** 会话设定没有显式填写身份时使用安全默认值，减少创建普通私聊规则所需的手工字段。 */
    private String defaultContextText(String value, String fallback, int maxLength) {
        String normalized = normalizeContextText(value, maxLength);
        return normalized.isBlank() ? fallback : normalized;
    }

    /** 只保留资产引用和使用条件，限制数量并过滤没有标识的无效资产。 */
    private List<ConversationProfileContext.AssetReference> normalizeAssetReferences(
            List<ConversationProfileContext.AssetReference> assets
    ) {
        if (assets == null) {
            return List.of();
        }
        return assets.stream()
                .filter(asset -> asset != null)
                .map(asset -> new ConversationProfileContext.AssetReference(
                        normalizeContextText(asset.assetId(), 255),
                        normalizeContextText(asset.type(), 64).toUpperCase(Locale.ROOT),
                        normalizeContextText(asset.name(), 255),
                        normalizeContextText(asset.description(), 1000),
                        normalizeContextText(asset.usageCondition(), 1000)
                ))
                .filter(asset -> !asset.assetId().isBlank() || !asset.name().isBlank())
                .limit(20)
                .toList();
    }

    /** 清理结构化上下文中的列表字段，并限制单项长度与总数量。 */
    private List<String> normalizeContextList(List<String> values, int maxItems, int maxLength) {
        if (values == null) {
            return List.of();
        }
        return values.stream()
                .map(value -> normalizeContextText(value, maxLength))
                .filter(value -> !value.isBlank())
                .distinct()
                .limit(maxItems)
                .toList();
    }

    /** 清理结构化文本并截断异常大输入，避免整个文档被误填入单个字段。 */
    private String normalizeContextText(String value, int maxLength) {
        String normalized = normalizeText(value);
        return normalized.length() <= maxLength ? normalized : normalized.substring(0, maxLength);
    }

    /** 对方可信度只接受四档稳定枚举，未知输入回退 UNKNOWN。 */
    private String normalizeTrustLevel(String value) {
        String normalized = normalizeText(value).toUpperCase(Locale.ROOT);
        return switch (normalized) {
            case "LOW", "MEDIUM", "HIGH" -> normalized;
            default -> "UNKNOWN";
        };
    }

    /** 审批模式只允许严格接管或自动纠偏，未知值一律回退严格模式。 */
    private String normalizeReviewMode(String value) {
        return "AUTO_REWRITE".equalsIgnoreCase(normalizeText(value)) ? "AUTO_REWRITE" : "STRICT_HANDOFF";
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
     * 限制单个会话可绑定的知识库来源数量和长度，避免将任意大文本直接写入设定集。
     * 实际读取只发生在 Python Runtime，并且会再次限制协议、响应体和检索片段大小。
     */
    private List<String> normalizeKnowledgeBaseSources(List<String> values) {
        if (values == null) {
            return List.of();
        }
        return values.stream()
                .filter(value -> value != null && !value.isBlank())
                .map(String::trim)
                .filter(value -> value.length() <= 2048)
                .distinct()
                .limit(8)
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
     * 这个函数的作用是限制单条私聊气泡最大字符数，防止模型配置意外把整段内容直接发送出去。
     */
    private Integer normalizeMaxReplyChars(Integer value) {
        if (value == null) {
            return 24;
        }
        return Math.min(Math.max(value, 8), 80);
    }

    /**
     * 这个函数的作用是把分段概率限制在 0 到 100，0 表示永不拆分，100 表示长回复总是拆分。
     */
    private Integer normalizeSplitReplyChancePercent(Integer value) {
        if (value == null) {
            return 33;
        }
        return Math.min(Math.max(value, 0), 100);
    }

    /**
     * 限制私聊上下文条数，避免一次回复把过多历史内容发给模型。
     */
    private Integer normalizeHistoryMaxMessages(Integer value) {
        if (value == null) {
            return 12;
        }
        return Math.min(Math.max(value, 1), 50);
    }

    /**
     * 限制注入模型的历史文本总长度，兼顾上下文连续性与调用成本。
     */
    private Integer normalizeHistoryMaxChars(Integer value) {
        if (value == null) {
            return 2000;
        }
        return Math.min(Math.max(value, 200), 12_000);
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
