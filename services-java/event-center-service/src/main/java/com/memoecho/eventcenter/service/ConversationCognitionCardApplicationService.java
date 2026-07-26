package com.memoecho.eventcenter.service;

import com.memoecho.eventcenter.dto.ConversationCognitionCardResponse;
import com.memoecho.eventcenter.dto.ConversationCognitionCardUpsertRequest;
import com.memoecho.eventcenter.model.ConversationCognitionCard;
import com.memoecho.eventcenter.repository.ConversationCognitionCardRepository;
import org.springframework.http.HttpStatus;
import org.springframework.stereotype.Service;
import org.springframework.transaction.annotation.Transactional;
import org.springframework.web.server.ResponseStatusException;

import java.time.Instant;
import java.util.LinkedHashSet;
import java.util.List;
import java.util.Locale;
import java.util.Optional;
import java.util.Set;
import java.util.UUID;

/**
 * 管理会话认知卡的字段来源、用户确认和模型增量合并。
 *
 * <p>用户覆盖的字段始终优先于模型推断。Runtime 只能更新未锁定字段，不能通过伪造 source
 * 覆盖用户已确认结论。</p>
 */
@Service
public class ConversationCognitionCardApplicationService {

    private static final Set<String> FIELD_SOURCES = Set.of(
            "GLOBAL_DEFAULT", "AI_INFERRED", "USER_CONFIRMED", "USER_OVERRIDE"
    );
    private static final String STATUS_INFERRED = "INFERRED";
    private static final String STATUS_CONFIRMED = "CONFIRMED";
    private static final String STATUS_USER_EDITED = "USER_EDITED";

    private final ConversationCognitionCardRepository repository;

    /** 注入认知卡仓储，应用层统一负责可信来源和合并规则。 */
    public ConversationCognitionCardApplicationService(ConversationCognitionCardRepository repository) {
        this.repository = repository;
    }

    /** 按当前用户和完整会话作用域读取认知卡，不存在时返回 404。 */
    public ConversationCognitionCardResponse get(
            String userId,
            String platform,
            String chatType,
            String chatId
    ) {
        Scope scope = normalizeScope(userId, platform, chatType, chatId);
        return repository.findByScope(scope.userId(), scope.platform(), scope.chatType(), scope.chatId())
                .map(this::toResponse)
                .orElseThrow(() -> new ResponseStatusException(HttpStatus.NOT_FOUND, "会话认知卡不存在"));
    }

    /** 按完整会话作用域查找认知卡；刷新服务使用空结果判断是否需要首次分析。 */
    public Optional<ConversationCognitionCardResponse> find(
            String userId,
            String platform,
            String chatType,
            String chatId
    ) {
        Scope scope = normalizeScope(userId, platform, chatType, chatId);
        return repository.findByScope(scope.userId(), scope.platform(), scope.chatType(), scope.chatId())
                .map(this::toResponse);
    }

    /**
     * 保存用户在桌面端做出的修正。
     *
     * <p>请求中显式提供的字段会被标为 USER_OVERRIDE 并锁定；未提供字段沿用旧值，避免编辑一个称呼时
     * 意外清空整张认知卡。</p>
     */
    @Transactional
    public ConversationCognitionCardResponse upsertByUser(
            String userId,
            ConversationCognitionCardUpsertRequest request
    ) {
        Scope scope = normalizeScope(userId, request.platform(), request.chatType(), request.chatId());
        ConversationCognitionCard existing = repository.findByScope(
                scope.userId(), scope.platform(), scope.chatType(), scope.chatId()).orElse(null);
        Instant now = Instant.now();
        ConversationCognitionCard saved = repository.save(new ConversationCognitionCard(
                existing == null ? UUID.randomUUID().toString() : existing.id(),
                scope.userId(), scope.platform(), scope.chatType(), scope.chatId(), 1,
                userField(request.relationship(), fieldOf(existing, FieldKind.RELATIONSHIP)),
                userField(request.preferredAddress(), fieldOf(existing, FieldKind.PREFERRED_ADDRESS)),
                userField(request.counterpartyTraits(), fieldOf(existing, FieldKind.COUNTERPARTY_TRAITS)),
                userField(request.ownerExpressionHabits(), fieldOf(existing, FieldKind.OWNER_EXPRESSION_HABITS)),
                userField(request.counterpartyExpressionHabits(), fieldOf(existing, FieldKind.COUNTERPARTY_EXPRESSION_HABITS)),
                userField(request.backgroundSummary(), fieldOf(existing, FieldKind.BACKGROUND_SUMMARY)),
                userField(request.currentProgress(), fieldOf(existing, FieldKind.CURRENT_PROGRESS)),
                providedOrExisting(request.knownFacts(), existing == null ? List.of() : existing.knownFacts(), 50, 500),
                providedOrExisting(request.recentTopics(), existing == null ? List.of() : existing.recentTopics(), 20, 300),
                providedOrExisting(request.openQuestions(), existing == null ? List.of() : existing.openQuestions(), 20, 500),
                existing == null ? List.of() : existing.sourceEventIds(),
                existing == null ? 0 : existing.sourceMessageCount(),
                STATUS_USER_EDITED,
                existing == null ? now : existing.analyzedAt(),
                existing == null ? now : existing.createdAt(),
                now
        ));
        return toResponse(saved);
    }

    /**
     * 合并 Runtime 最新推断。
     *
     * <p>Runtime 请求体里的 source 和 locked 不可信，因此统一重写为 AI_INFERRED/false；旧字段只要被
     * 用户锁定就原样保留。这个规则是防止模型刷新导致“人物关系反复变化”的核心边界。</p>
     */
    @Transactional
    public ConversationCognitionCardResponse upsertInference(
            String userId,
            ConversationCognitionCardUpsertRequest request
    ) {
        Scope scope = normalizeScope(userId, request.platform(), request.chatType(), request.chatId());
        ConversationCognitionCard existing = repository.findByScope(
                scope.userId(), scope.platform(), scope.chatType(), scope.chatId()).orElse(null);
        Instant now = Instant.now();
        ConversationCognitionCard saved = repository.save(new ConversationCognitionCard(
                existing == null ? UUID.randomUUID().toString() : existing.id(),
                scope.userId(), scope.platform(), scope.chatType(), scope.chatId(), 1,
                inferredField(request.relationship(), fieldOf(existing, FieldKind.RELATIONSHIP)),
                inferredField(request.preferredAddress(), fieldOf(existing, FieldKind.PREFERRED_ADDRESS)),
                inferredField(request.counterpartyTraits(), fieldOf(existing, FieldKind.COUNTERPARTY_TRAITS)),
                inferredField(request.ownerExpressionHabits(), fieldOf(existing, FieldKind.OWNER_EXPRESSION_HABITS)),
                inferredField(request.counterpartyExpressionHabits(), fieldOf(existing, FieldKind.COUNTERPARTY_EXPRESSION_HABITS)),
                inferredField(request.backgroundSummary(), fieldOf(existing, FieldKind.BACKGROUND_SUMMARY)),
                inferredField(request.currentProgress(), fieldOf(existing, FieldKind.CURRENT_PROGRESS)),
                providedOrExisting(request.knownFacts(), existing == null ? List.of() : existing.knownFacts(), 50, 500),
                providedOrExisting(request.recentTopics(), existing == null ? List.of() : existing.recentTopics(), 20, 300),
                providedOrExisting(request.openQuestions(), existing == null ? List.of() : existing.openQuestions(), 20, 500),
                providedOrExisting(request.sourceEventIds(), existing == null ? List.of() : existing.sourceEventIds(), 120, 255),
                request.sourceMessageCount() == null && existing != null
                        ? existing.sourceMessageCount()
                        : normalizeCount(request.sourceMessageCount()),
                existing != null && STATUS_CONFIRMED.equals(existing.status()) ? STATUS_CONFIRMED : STATUS_INFERRED,
                now,
                existing == null ? now : existing.createdAt(),
                now
        ));
        return toResponse(saved);
    }

    /** 用户确认当前认知卡，把所有已有结论升级为 USER_CONFIRMED 并锁定。 */
    @Transactional
    public ConversationCognitionCardResponse confirm(
            String userId,
            String platform,
            String chatType,
            String chatId
    ) {
        Scope scope = normalizeScope(userId, platform, chatType, chatId);
        ConversationCognitionCard existing = repository.findByScope(
                        scope.userId(), scope.platform(), scope.chatType(), scope.chatId())
                .orElseThrow(() -> new ResponseStatusException(HttpStatus.NOT_FOUND, "会话认知卡不存在"));
        Instant now = Instant.now();
        ConversationCognitionCard confirmed = new ConversationCognitionCard(
                existing.id(), existing.userId(), existing.platform(), existing.chatType(), existing.chatId(),
                existing.version(), confirmField(existing.relationship()), confirmField(existing.preferredAddress()),
                confirmField(existing.counterpartyTraits()), confirmField(existing.ownerExpressionHabits()),
                confirmField(existing.counterpartyExpressionHabits()), confirmField(existing.backgroundSummary()),
                confirmField(existing.currentProgress()), existing.knownFacts(), existing.recentTopics(),
                existing.openQuestions(), existing.sourceEventIds(), existing.sourceMessageCount(), STATUS_CONFIRMED,
                existing.analyzedAt(), existing.createdAt(), now
        );
        return toResponse(repository.save(confirmed));
    }

    /** 删除当前用户指定会话的认知卡。 */
    public void delete(String userId, String platform, String chatType, String chatId) {
        Scope scope = normalizeScope(userId, platform, chatType, chatId);
        if (repository.deleteByScope(scope.userId(), scope.platform(), scope.chatType(), scope.chatId()) == 0) {
            throw new ResponseStatusException(HttpStatus.NOT_FOUND, "会话认知卡不存在");
        }
    }

    /** 用户字段只采用文本值，来源和锁定状态由服务端强制确定。 */
    private ConversationCognitionCard.CognitionField userField(
            ConversationCognitionCard.CognitionField incoming,
            ConversationCognitionCard.CognitionField existing
    ) {
        if (incoming == null) {
            return safeField(existing);
        }
        return new ConversationCognitionCard.CognitionField(
                normalizeText(incoming.value(), 2000), "USER_OVERRIDE", 1.0d, true);
    }

    /** 模型字段不能覆盖已锁定值；其置信度被限制在 0 到 1。 */
    private ConversationCognitionCard.CognitionField inferredField(
            ConversationCognitionCard.CognitionField incoming,
            ConversationCognitionCard.CognitionField existing
    ) {
        ConversationCognitionCard.CognitionField safeExisting = safeField(existing);
        if (safeExisting.locked() || incoming == null) {
            return safeExisting;
        }
        return new ConversationCognitionCard.CognitionField(
                normalizeText(incoming.value(), 2000), "AI_INFERRED", normalizeConfidence(incoming.confidence()), false);
    }

    /** 确认空字段没有意义，空值保持可供后续模型推断。 */
    private ConversationCognitionCard.CognitionField confirmField(ConversationCognitionCard.CognitionField field) {
        ConversationCognitionCard.CognitionField safeField = safeField(field);
        if (safeField.value().isBlank()) {
            return safeField;
        }
        return new ConversationCognitionCard.CognitionField(
                safeField.value(), "USER_CONFIRMED", Math.max(0.95d, safeField.confidence()), true);
    }

    /** 返回已有字段；新卡没有旧值时返回统一空字段。 */
    private ConversationCognitionCard.CognitionField fieldOf(
            ConversationCognitionCard card,
            FieldKind kind
    ) {
        if (card == null) {
            return ConversationCognitionCard.CognitionField.empty();
        }
        return switch (kind) {
            case RELATIONSHIP -> card.relationship();
            case PREFERRED_ADDRESS -> card.preferredAddress();
            case COUNTERPARTY_TRAITS -> card.counterpartyTraits();
            case OWNER_EXPRESSION_HABITS -> card.ownerExpressionHabits();
            case COUNTERPARTY_EXPRESSION_HABITS -> card.counterpartyExpressionHabits();
            case BACKGROUND_SUMMARY -> card.backgroundSummary();
            case CURRENT_PROGRESS -> card.currentProgress();
        };
    }

    /** 清理历史空字段并校验来源枚举；未知来源降级为 AI_INFERRED。 */
    private ConversationCognitionCard.CognitionField safeField(ConversationCognitionCard.CognitionField field) {
        if (field == null) {
            return ConversationCognitionCard.CognitionField.empty();
        }
        String source = normalizeOptional(field.source()).toUpperCase(Locale.ROOT);
        source = FIELD_SOURCES.contains(source) ? source : "AI_INFERRED";
        return new ConversationCognitionCard.CognitionField(
                normalizeText(field.value(), 2000), source, normalizeConfidence(field.confidence()), field.locked());
    }

    /** 请求没有提供列表时保留旧值；显式提供空列表时允许用户清空。 */
    private List<String> providedOrExisting(
            List<String> incoming,
            List<String> existing,
            int maxItems,
            int maxLength
    ) {
        return incoming == null ? List.copyOf(existing) : normalizeList(incoming, maxItems, maxLength);
    }

    /** 清理、去重并限制模型或用户提交的列表，防止把整段历史塞入认知卡。 */
    private List<String> normalizeList(List<String> values, int maxItems, int maxLength) {
        LinkedHashSet<String> normalized = new LinkedHashSet<>();
        for (String value : values == null ? List.<String>of() : values) {
            String item = normalizeText(value, maxLength);
            if (!item.isBlank()) {
                normalized.add(item);
            }
            if (normalized.size() >= maxItems) {
                break;
            }
        }
        return List.copyOf(normalized);
    }

    /** 规范完整会话作用域，所有比较统一使用小写平台和会话类型。 */
    private Scope normalizeScope(String userId, String platform, String chatType, String chatId) {
        return new Scope(
                requireText(userId, "用户标识", 128),
                requireText(platform, "平台", 64).toLowerCase(Locale.ROOT),
                requireText(chatType, "会话类型", 32).toLowerCase(Locale.ROOT),
                requireText(chatId, "会话标识", 255)
        );
    }

    /** 校验必填文本并限制长度。 */
    private String requireText(String value, String name, int maxLength) {
        String normalized = normalizeText(value, maxLength);
        if (normalized.isBlank()) {
            throw new ResponseStatusException(HttpStatus.BAD_REQUEST, name + "不能为空");
        }
        return normalized;
    }

    /** 清理可选文本并截断异常输入。 */
    private String normalizeText(String value, int maxLength) {
        String normalized = normalizeOptional(value);
        return normalized.length() <= maxLength ? normalized : normalized.substring(0, maxLength);
    }

    /** 把 null 文本规范为空字符串。 */
    private String normalizeOptional(String value) {
        return value == null ? "" : value.trim();
    }

    /** 限制置信度，NaN 和无穷值安全回退为 0。 */
    private double normalizeConfidence(double value) {
        if (!Double.isFinite(value)) {
            return 0.0d;
        }
        return Math.max(0.0d, Math.min(1.0d, value));
    }

    /** 限制来源消息数，避免负值或异常大值污染统计。 */
    private int normalizeCount(Integer value) {
        return value == null ? 0 : Math.max(0, Math.min(10_000, value));
    }

    /** 把领域对象转换为不暴露 userId 的 API 响应。 */
    private ConversationCognitionCardResponse toResponse(ConversationCognitionCard card) {
        return new ConversationCognitionCardResponse(
                card.id(), card.platform(), card.chatType(), card.chatId(), card.version(), card.relationship(),
                card.preferredAddress(), card.counterpartyTraits(), card.ownerExpressionHabits(),
                card.counterpartyExpressionHabits(), card.backgroundSummary(), card.currentProgress(),
                card.knownFacts(), card.recentTopics(), card.openQuestions(), card.sourceEventIds(),
                card.sourceMessageCount(), card.status(), card.analyzedAt(), card.createdAt(), card.updatedAt()
        );
    }

    /** 字段选择仅在服务内部使用，避免重复七套空值分支。 */
    private enum FieldKind {
        RELATIONSHIP,
        PREFERRED_ADDRESS,
        COUNTERPARTY_TRAITS,
        OWNER_EXPRESSION_HABITS,
        COUNTERPARTY_EXPRESSION_HABITS,
        BACKGROUND_SUMMARY,
        CURRENT_PROGRESS
    }

    /** 规范化后的会话唯一键。 */
    private record Scope(String userId, String platform, String chatType, String chatId) {
    }
}
