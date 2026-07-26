package com.memoecho.eventcenter.service;

import com.fasterxml.jackson.core.JsonProcessingException;
import com.fasterxml.jackson.core.type.TypeReference;
import com.fasterxml.jackson.databind.ObjectMapper;
import com.memoecho.eventcenter.dto.MemoryCandidateRejectRequest;
import com.memoecho.eventcenter.dto.MemoryCandidateEvidenceResponse;
import com.memoecho.eventcenter.dto.MemoryCandidateResponse;
import com.memoecho.eventcenter.dto.MemoryCandidateUpsertRequest;
import com.memoecho.eventcenter.dto.MemoryConflictResolutionRequest;
import com.memoecho.eventcenter.dto.MemoryConflictResolutionResponse;
import com.memoecho.eventcenter.dto.ConversationMessageResponse;
import com.memoecho.eventcenter.model.MemoryCandidate;
import com.memoecho.eventcenter.model.StoredEvent;
import com.memoecho.eventcenter.repository.EventRecordRepository;
import com.memoecho.eventcenter.repository.MemoryCandidateRepository;
import org.springframework.http.HttpStatus;
import org.springframework.stereotype.Service;
import org.springframework.transaction.annotation.Transactional;
import org.springframework.web.server.ResponseStatusException;

import java.time.Instant;
import java.util.ArrayList;
import java.util.Comparator;
import java.util.LinkedHashMap;
import java.util.LinkedHashSet;
import java.util.List;
import java.util.Locale;
import java.util.Set;
import java.util.UUID;

/**
 * 长期记忆候选应用服务。
 *
 * <p>该服务负责状态流转、来源可信度和作用域匹配。数据库只保存事实，Runtime 只能读取经过
 * 用户确认的 VERIFIED 记录，从而避免模型输出、群友陈述或未确认推断污染长期记忆。</p>
 */
@Service
public class MemoryCandidateApplicationService {

    private static final String CANDIDATE = "CANDIDATE";
    private static final String VERIFIED = "VERIFIED";
    private static final String REJECTED = "REJECTED";
    private static final String SUPERSEDED = "SUPERSEDED";
    private static final String KEEP_VERIFIED = "KEEP_VERIFIED";
    private static final String USE_CANDIDATE = "USE_CANDIDATE";
    private static final Set<String> SCOPE_TYPES = Set.of("GLOBAL", "PLATFORM", "SCENE", "CONVERSATION");

    private final MemoryCandidateRepository repository;
    private final EventRecordRepository eventRecordRepository;
    private final EventCenterApplicationService eventCenterApplicationService;
    private final ObjectMapper objectMapper;

    /** 注入持久化仓储和 JSON 编解码器，证据事件以稳定数组格式保存。 */
    public MemoryCandidateApplicationService(
            MemoryCandidateRepository repository,
            EventRecordRepository eventRecordRepository,
            EventCenterApplicationService eventCenterApplicationService,
            ObjectMapper objectMapper
    ) {
        this.repository = repository;
        this.eventRecordRepository = eventRecordRepository;
        this.eventCenterApplicationService = eventCenterApplicationService;
        this.objectMapper = objectMapper;
    }

    /** 列出当前用户的候选记忆，并在读取前把已到期记录标为 EXPIRED。 */
    public List<MemoryCandidateResponse> list(String userId, String status) {
        String normalizedUserId = normalizeRequired(userId, "用户标识");
        repository.expireDueMemories(normalizedUserId, Instant.now());
        String normalizedStatus = normalizeOptionalUpper(status);
        return repository.findAllByUserIdAndStatus(normalizedUserId, normalizedStatus).stream()
                .map(this::toResponse)
                .toList();
    }

    /** 创建用户手工录入的候选；手工录入仍需显式确认后才会提供给 Runtime。 */
    public MemoryCandidateResponse create(String userId, MemoryCandidateUpsertRequest request) {
        return createInternal(userId, request, false);
    }

    /**
     * 创建 Runtime 提取的候选。
     *
     * <p>仅接受 OWNER 的 human_self 证据。CONTACT、GROUP_MEMBER 和 agent_output 即使置信度很高，
     * 也不能在此冒充账号主人事实。</p>
     */
    public MemoryCandidateResponse createFromRuntime(String userId, MemoryCandidateUpsertRequest request) {
        return createInternal(userId, request, true);
    }

    /** 编辑候选内容或作用域；已经确认、拒绝或过期的记录必须重新创建候选，保留审计含义。 */
    public MemoryCandidateResponse update(String userId, String id, MemoryCandidateUpsertRequest request) {
        MemoryCandidate existing = findOwned(userId, id);
        ensureCandidate(existing);
        Instant now = Instant.now();
        MemoryCandidateUpsertRequest trustedRequest = preserveSourceEvidence(existing, request);
        MemoryCandidate updated = buildCandidate(
                existing.id(), existing.userId(), trustedRequest, existing.status(), existing.rejectionReason(),
                existing.firstSeenAt(), now, existing.createdAt(), now,
                existing.sourceActorType(), existing.factAuthority(), true
        );
        return toResponse(repository.save(updated));
    }

    /** 编辑候选时只接受事实和作用域字段，来源证据、权威级别与置信度沿用原记录。 */
    private MemoryCandidateUpsertRequest preserveSourceEvidence(
            MemoryCandidate existing,
            MemoryCandidateUpsertRequest request
    ) {
        return new MemoryCandidateUpsertRequest(
                request.subject(), request.predicate(), request.value(), request.scopeType(), request.platform(),
                request.scene(), request.chatType(), request.chatId(), readSourceEventIds(existing.sourceEventIdsJson()),
                existing.sourceActorType(), existing.factAuthority(), existing.confidence(), request.expiresAt()
        );
    }

    /** 用户确认候选事实，确认后它才具备 Runtime 可读资格。 */
    public MemoryCandidateResponse verify(String userId, String id) {
        MemoryCandidate existing = findOwned(userId, id);
        ensureCandidate(existing);
        if (!findConflictingVerified(existing).isEmpty()) {
            throw new ResponseStatusException(
                    HttpStatus.CONFLICT,
                    "候选值与已确认记忆冲突，请先选择保留旧值或采用候选值"
            );
        }
        Instant now = Instant.now();
        if (existing.expiresAt() != null && !existing.expiresAt().isAfter(now)) {
            repository.expireDueMemories(existing.userId(), now);
            throw new ResponseStatusException(HttpStatus.CONFLICT, "候选记忆已经过期");
        }
        return toResponse(repository.save(copyWithStatus(existing, VERIFIED, "", now)));
    }

    /**
     * 聚合候选记忆的真实来源上下文。
     * 每个来源只暴露有限半径内的消息，多条来源窗口重叠时按事件 ID 去重，避免把整段历史聊天交给客户端。
     */
    public MemoryCandidateEvidenceResponse evidence(String userId, String id, Integer radius) {
        MemoryCandidate candidate = findOwned(userId, id);
        List<String> sourceEventIds = readSourceEventIds(candidate.sourceEventIdsJson());
        LinkedHashMap<String, ConversationMessageResponse> messages = new LinkedHashMap<>();
        List<String> missingEventIds = new ArrayList<>();
        for (String sourceEventId : sourceEventIds) {
            List<ConversationMessageResponse> context = eventCenterApplicationService
                    .findConversationContextAroundEvent(candidate.userId(), sourceEventId, radius);
            boolean sourcePresent = context.stream().anyMatch(message -> sourceEventId.equals(message.eventId()));
            if (!sourcePresent) {
                missingEventIds.add(sourceEventId);
            }
            for (ConversationMessageResponse message : context) {
                messages.putIfAbsent(message.eventId(), message);
            }
        }
        List<ConversationMessageResponse> orderedMessages = messages.values().stream()
                .sorted(Comparator.comparing(
                        ConversationMessageResponse::timestamp,
                        Comparator.nullsFirst(String::compareTo)
                ))
                .toList();
        return new MemoryCandidateEvidenceResponse(
                candidate.id(), sourceEventIds, orderedMessages, List.copyOf(missingEventIds));
    }

    /**
     * 原子处理候选值与已确认值的冲突。
     * 保留旧值会拒绝候选；采用候选值会先把全部冲突旧值标为已替代，再确认候选，事务失败时不会留下双重事实。
     */
    @Transactional
    public MemoryConflictResolutionResponse resolveConflict(
            String userId,
            String id,
            MemoryConflictResolutionRequest request
    ) {
        MemoryCandidate candidate = findOwned(userId, id);
        ensureCandidate(candidate);
        String decision = normalizeOptionalUpper(request == null ? null : request.decision());
        if (!Set.of(KEEP_VERIFIED, USE_CANDIDATE).contains(decision)) {
            throw new ResponseStatusException(HttpStatus.BAD_REQUEST, "不支持的记忆冲突处理方式");
        }
        List<MemoryCandidate> conflicts = findConflictingVerified(candidate);
        if (conflicts.isEmpty()) {
            throw new ResponseStatusException(HttpStatus.CONFLICT, "当前候选已经不存在已确认值冲突");
        }
        Instant now = Instant.now();
        if (KEEP_VERIFIED.equals(decision)) {
            MemoryCandidate rejected = repository.save(copyWithStatus(
                    candidate, REJECTED, "用户选择保留原已确认值", now));
            return new MemoryConflictResolutionResponse(toResponse(rejected), List.of());
        }

        List<String> supersededIds = new ArrayList<>();
        for (MemoryCandidate conflict : conflicts) {
            repository.save(copyWithStatus(
                    conflict, SUPERSEDED, "已被候选记忆 " + candidate.id() + " 替代", now));
            supersededIds.add(conflict.id());
        }
        MemoryCandidate verified = repository.save(copyWithStatus(candidate, VERIFIED, "", now));
        return new MemoryConflictResolutionResponse(toResponse(verified), List.copyOf(supersededIds));
    }

    /** 用户拒绝候选事实，拒绝原因只用于管理界面，不会注入 Agent。 */
    public MemoryCandidateResponse reject(String userId, String id, MemoryCandidateRejectRequest request) {
        MemoryCandidate existing = findOwned(userId, id);
        ensureCandidate(existing);
        return toResponse(repository.save(copyWithStatus(
                existing, REJECTED, normalizeOptional(request == null ? null : request.reason()), Instant.now())));
    }

    /** 永久删除当前用户拥有的一条记忆记录。 */
    public void delete(String userId, String id) {
        if (repository.deleteByIdAndUserId(
                normalizeRequired(id, "记忆标识"), normalizeRequired(userId, "用户标识")) == 0) {
            throw new ResponseStatusException(HttpStatus.NOT_FOUND, "长期记忆不存在");
        }
    }

    /** 返回适用于当前会话的已确认记忆，候选、拒绝和过期数据不会出现在结果中。 */
    public List<MemoryCandidateResponse> listVerifiedForRuntime(
            String userId,
            String platform,
            String scene,
            String chatType,
            String chatId
    ) {
        String normalizedUserId = normalizeRequired(userId, "用户标识");
        Instant now = Instant.now();
        repository.expireDueMemories(normalizedUserId, now);
        return repository.findVerifiedByUserId(normalizedUserId, now).stream()
                .filter(memory -> matchesScope(memory, platform, scene, chatType, chatId))
                .map(this::toResponse)
                .toList();
    }

    /** 创建候选的共享实现，并根据调用方类型执行不同的来源校验。 */
    private MemoryCandidateResponse createInternal(
            String userId,
            MemoryCandidateUpsertRequest request,
            boolean runtimeRequest
    ) {
        Instant now = Instant.now();
        String sourceActorType = normalizeOptionalUpper(request.sourceActorType());
        String factAuthority = normalizeOptional(request.factAuthority()).toLowerCase(Locale.ROOT);
        if (runtimeRequest) {
            if (!"OWNER".equals(sourceActorType) || !"human_self".equals(factAuthority)) {
                throw new ResponseStatusException(
                        HttpStatus.BAD_REQUEST,
                        "Runtime 只能从 OWNER 的 human_self 消息创建长期记忆候选"
                );
            }
            if (normalizeSourceEventIds(request.sourceEventIds()).isEmpty()) {
                throw new ResponseStatusException(HttpStatus.BAD_REQUEST, "Runtime 候选必须携带来源事件");
            }
            validateRuntimeEvidence(userId, request.sourceEventIds());
        } else {
            sourceActorType = sourceActorType.isBlank() ? "OWNER" : sourceActorType;
            factAuthority = factAuthority.isBlank() ? "human_self" : factAuthority;
        }
        MemoryCandidate candidate = buildCandidate(
                UUID.randomUUID().toString(), normalizeRequired(userId, "用户标识"), request,
                CANDIDATE, "", now, now, now, now, sourceActorType, factAuthority, runtimeRequest
        );
        if (runtimeRequest) {
            for (MemoryCandidate existing : repository.findActiveByFactKey(
                    candidate.userId(), candidate.subject(), candidate.predicate(), candidate.scopeType(),
                    candidate.platform(), candidate.scene(), candidate.chatType(), candidate.chatId())) {
                if (existing.value().equals(candidate.value())) {
                    return toResponse(repository.save(mergeRuntimeEvidence(existing, candidate, now)));
                }
            }
        }
        return toResponse(repository.save(candidate));
    }

    /**
     * 回查 Runtime 提交的每条来源事件，不能只相信请求体中自报的 OWNER 标记。
     *
     * <p>事件必须属于当前用户，并且同时由连接器标记为 OWNER、由事件中心归类为 USER_MANUAL。
     * Agent 自动发送、人工确认草稿、联系人消息和不存在的事件都会被拒绝。</p>
     */
    private void validateRuntimeEvidence(String userId, List<String> sourceEventIds) {
        String normalizedUserId = normalizeRequired(userId, "用户标识");
        for (String eventId : normalizeSourceEventIds(sourceEventIds)) {
            StoredEvent sourceEvent = eventRecordRepository.findByEventId(eventId)
                    .orElseThrow(() -> new ResponseStatusException(
                            HttpStatus.BAD_REQUEST, "长期记忆来源事件不存在: " + eventId));
            String actorType = sourceEvent.payload() == null
                    ? ""
                    : normalizeOptionalUpper(sourceEvent.payload().actorType());
            if (!normalizedUserId.equals(sourceEvent.ownerUserId())
                    || !"OWNER".equals(actorType)
                    || !"USER_MANUAL".equalsIgnoreCase(sourceEvent.messageOrigin())) {
                throw new ResponseStatusException(
                        HttpStatus.BAD_REQUEST,
                        "长期记忆来源必须是当前用户手工发送的 OWNER 消息"
                );
            }
        }
    }

    /**
     * 合并同一事实的重复证据。
     *
     * <p>已确认事实不会因新模型输出而改变值、状态或到期时间；候选事实只补充证据、最后出现时间和更高置信度。</p>
     */
    private MemoryCandidate mergeRuntimeEvidence(
            MemoryCandidate existing,
            MemoryCandidate incoming,
            Instant now
    ) {
        LinkedHashSet<String> sourceEventIds = new LinkedHashSet<>(readSourceEventIds(existing.sourceEventIdsJson()));
        sourceEventIds.addAll(readSourceEventIds(incoming.sourceEventIdsJson()));
        Instant expiresAt = VERIFIED.equals(existing.status())
                ? existing.expiresAt()
                : (incoming.expiresAt() == null ? existing.expiresAt() : incoming.expiresAt());
        return new MemoryCandidate(
                existing.id(), existing.userId(), existing.subject(), existing.predicate(), existing.value(),
                existing.scopeType(), existing.platform(), existing.scene(), existing.chatType(), existing.chatId(),
                writeSourceEventIds(List.copyOf(sourceEventIds)), existing.sourceActorType(), existing.factAuthority(),
                Math.max(existing.confidence(), incoming.confidence()), existing.status(), existing.rejectionReason(),
                existing.firstSeenAt(), now, expiresAt, existing.createdAt(), now
        );
    }

    /** 把请求规范化成完整领域对象，并校验作用域所需的定位字段。 */
    private MemoryCandidate buildCandidate(
            String id,
            String userId,
            MemoryCandidateUpsertRequest request,
            String status,
            String rejectionReason,
            Instant firstSeenAt,
            Instant lastSeenAt,
            Instant createdAt,
            Instant updatedAt,
            String fallbackActorType,
            String fallbackFactAuthority,
            boolean preserveTrustedSource
    ) {
        String scopeType = normalizeOptionalUpper(request.scopeType());
        scopeType = scopeType.isBlank() ? (preserveTrustedSource ? "CONVERSATION" : "GLOBAL") : scopeType;
        if (!SCOPE_TYPES.contains(scopeType)) {
            throw new ResponseStatusException(HttpStatus.BAD_REQUEST, "不支持的记忆作用域");
        }
        String platform = normalizeOptional(request.platform()).toLowerCase(Locale.ROOT);
        String scene = normalizeOptional(request.scene()).toLowerCase(Locale.ROOT);
        String chatType = normalizeOptional(request.chatType()).toLowerCase(Locale.ROOT);
        String chatId = normalizeOptional(request.chatId());
        validateScope(scopeType, platform, scene, chatType, chatId);
        String actorType = preserveTrustedSource
                ? fallbackActorType
                : defaultValue(normalizeOptionalUpper(request.sourceActorType()), fallbackActorType);
        String authority = preserveTrustedSource
                ? fallbackFactAuthority
                : defaultValue(normalizeOptional(request.factAuthority()).toLowerCase(Locale.ROOT), fallbackFactAuthority);
        double confidence = request.confidence() == null ? 0.5d : request.confidence();
        return new MemoryCandidate(
                id, userId, normalizeRequired(request.subject(), "记忆主体"),
                normalizeRequired(request.predicate(), "记忆属性"), normalizeRequired(request.value(), "记忆内容"),
                scopeType, platform, scene, chatType, chatId, writeSourceEventIds(request.sourceEventIds()),
                actorType, authority, confidence, status, rejectionReason,
                firstSeenAt, lastSeenAt, request.expiresAt(), createdAt, updatedAt
        );
    }

    /** 检查作用域定位字段，防止一个标为会话级的事实意外扩散到所有平台。 */
    private void validateScope(String scopeType, String platform, String scene, String chatType, String chatId) {
        if ("PLATFORM".equals(scopeType) && platform.isBlank()) {
            throw new ResponseStatusException(HttpStatus.BAD_REQUEST, "平台级记忆必须指定 platform");
        }
        if ("SCENE".equals(scopeType) && scene.isBlank()) {
            throw new ResponseStatusException(HttpStatus.BAD_REQUEST, "场景级记忆必须指定 scene");
        }
        if ("CONVERSATION".equals(scopeType)
                && (platform.isBlank() || chatType.isBlank() || chatId.isBlank())) {
            throw new ResponseStatusException(HttpStatus.BAD_REQUEST, "会话级记忆必须指定平台、会话类型和会话 ID");
        }
    }

    /** 根据当前事件位置判断一条已确认记忆是否可以进入本轮上下文。 */
    private boolean matchesScope(
            MemoryCandidate memory,
            String platform,
            String scene,
            String chatType,
            String chatId
    ) {
        String currentPlatform = normalizeOptional(platform).toLowerCase(Locale.ROOT);
        String currentScene = normalizeOptional(scene).toLowerCase(Locale.ROOT);
        String currentChatType = normalizeOptional(chatType).toLowerCase(Locale.ROOT);
        String currentChatId = normalizeOptional(chatId);
        return switch (memory.scopeType()) {
            case "GLOBAL" -> true;
            case "PLATFORM" -> memory.platform().equals(currentPlatform);
            case "SCENE" -> memory.scene().equals(currentScene)
                    && (memory.platform().isBlank() || memory.platform().equals(currentPlatform));
            case "CONVERSATION" -> memory.platform().equals(currentPlatform)
                    && memory.chatType().equals(currentChatType)
                    && memory.chatId().equals(currentChatId);
            default -> false;
        };
    }

    /** 查找事实键完全相同但值不同的已确认记忆，供直接确认保护和冲突决策共同使用。 */
    private List<MemoryCandidate> findConflictingVerified(MemoryCandidate candidate) {
        return repository.findActiveByFactKey(
                        candidate.userId(), candidate.subject(), candidate.predicate(), candidate.scopeType(),
                        candidate.platform(), candidate.scene(), candidate.chatType(), candidate.chatId())
                .stream()
                .filter(memory -> VERIFIED.equals(memory.status()))
                .filter(memory -> !memory.value().equals(candidate.value()))
                .toList();
    }

    /** 复制记忆并只改变状态相关字段，确保确认操作不改写原始证据。 */
    private MemoryCandidate copyWithStatus(
            MemoryCandidate existing,
            String status,
            String rejectionReason,
            Instant updatedAt
    ) {
        return new MemoryCandidate(
                existing.id(), existing.userId(), existing.subject(), existing.predicate(), existing.value(),
                existing.scopeType(), existing.platform(), existing.scene(), existing.chatType(), existing.chatId(),
                existing.sourceEventIdsJson(), existing.sourceActorType(), existing.factAuthority(),
                existing.confidence(), status, rejectionReason, existing.firstSeenAt(), existing.lastSeenAt(),
                existing.expiresAt(), existing.createdAt(), updatedAt
        );
    }

    /** 查找并校验记录所有权，跨用户 ID 统一按不存在处理。 */
    private MemoryCandidate findOwned(String userId, String id) {
        return repository.findByIdAndUserId(
                        normalizeRequired(id, "记忆标识"), normalizeRequired(userId, "用户标识"))
                .orElseThrow(() -> new ResponseStatusException(HttpStatus.NOT_FOUND, "长期记忆不存在"));
    }

    /** 只有候选状态允许编辑、确认或拒绝，防止无审计地反复改写已确认事实。 */
    private void ensureCandidate(MemoryCandidate candidate) {
        if (!CANDIDATE.equals(candidate.status())) {
            throw new ResponseStatusException(HttpStatus.CONFLICT, "只有候选状态的记忆可以执行该操作");
        }
    }

    /** 转换为 API 响应，并安全解析证据事件数组。 */
    private MemoryCandidateResponse toResponse(MemoryCandidate candidate) {
        return new MemoryCandidateResponse(
                candidate.id(), candidate.subject(), candidate.predicate(), candidate.value(), candidate.scopeType(),
                candidate.platform(), candidate.scene(), candidate.chatType(), candidate.chatId(),
                readSourceEventIds(candidate.sourceEventIdsJson()), candidate.sourceActorType(),
                candidate.factAuthority(), candidate.confidence(), candidate.status(), candidate.rejectionReason(),
                candidate.firstSeenAt(), candidate.lastSeenAt(), candidate.expiresAt(), candidate.createdAt(),
                candidate.updatedAt()
        );
    }

    /** 去重并清理来源事件 ID，保持证据顺序稳定。 */
    private List<String> normalizeSourceEventIds(List<String> values) {
        LinkedHashSet<String> normalized = new LinkedHashSet<>();
        for (String value : values == null ? List.<String>of() : values) {
            String item = normalizeOptional(value);
            if (!item.isBlank()) {
                normalized.add(item);
            }
        }
        return List.copyOf(normalized);
    }

    /** 把证据事件数组序列化为数据库文本。 */
    private String writeSourceEventIds(List<String> values) {
        try {
            return objectMapper.writeValueAsString(normalizeSourceEventIds(values));
        } catch (JsonProcessingException exception) {
            throw new IllegalStateException("长期记忆来源事件序列化失败", exception);
        }
    }

    /** 解析历史数据中的证据事件；损坏数据降级为空数组而不是阻断全部记忆读取。 */
    private List<String> readSourceEventIds(String json) {
        try {
            return objectMapper.readValue(json, new TypeReference<>() { });
        } catch (JsonProcessingException exception) {
            return List.of();
        }
    }

    /** 校验必填文本并去除首尾空白。 */
    private String normalizeRequired(String value, String fieldName) {
        String normalized = normalizeOptional(value);
        if (normalized.isBlank()) {
            throw new ResponseStatusException(HttpStatus.BAD_REQUEST, fieldName + "不能为空");
        }
        return normalized;
    }

    /** 规范可选文本。 */
    private String normalizeOptional(String value) {
        return value == null ? "" : value.trim();
    }

    /** 规范枚举型可选文本。 */
    private String normalizeOptionalUpper(String value) {
        return normalizeOptional(value).toUpperCase(Locale.ROOT);
    }

    /** 当请求没有显式新值时沿用可信旧值。 */
    private String defaultValue(String value, String fallback) {
        return value.isBlank() ? fallback : value;
    }
}
