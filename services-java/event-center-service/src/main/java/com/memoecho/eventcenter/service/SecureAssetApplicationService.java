package com.memoecho.eventcenter.service;

import com.memoecho.eventcenter.dto.SecureAssetResponse;
import com.memoecho.eventcenter.dto.SecureAssetRuntimeResponse;
import com.memoecho.eventcenter.dto.SecureAssetUpsertRequest;
import com.memoecho.eventcenter.model.SecureAsset;
import com.memoecho.eventcenter.repository.SecureAssetRepository;
import org.springframework.http.HttpStatus;
import org.springframework.stereotype.Service;
import org.springframework.transaction.annotation.Transactional;
import org.springframework.web.server.ResponseStatusException;

import java.time.Instant;
import java.util.List;
import java.util.Locale;
import java.util.UUID;

/**
 * 安全资产应用服务，统一处理所有权、加密、库存与响应脱敏。
 */
@Service
public class SecureAssetApplicationService {

    private static final String REUSABLE = "REUSABLE";
    private static final String SINGLE_USE = "SINGLE_USE";

    private final SecureAssetRepository repository;
    private final AssetPayloadCryptoService cryptoService;

    /** 注入资产仓储和加密服务，控制器不接触密文。 */
    public SecureAssetApplicationService(
            SecureAssetRepository repository,
            AssetPayloadCryptoService cryptoService
    ) {
        this.repository = repository;
        this.cryptoService = cryptoService;
    }

    /** 返回指定用户的资产元数据列表，不返回正文和密文。 */
    public List<SecureAssetResponse> listAssets(String userId) {
        return repository.findAllByUserId(normalizeUserId(userId)).stream()
                .map(this::toResponse)
                .toList();
    }

    /** 返回指定用户拥有的一条资产元数据，不存在或不属于该用户时统一返回 404。 */
    public SecureAssetResponse getAsset(String userId, String assetId) {
        return toResponse(findOwnedAsset(userId, assetId));
    }

    /** 创建安全资产，并在首次落库前加密正文。 */
    public SecureAssetResponse createAsset(String userId, SecureAssetUpsertRequest request) {
        String content = normalizeContent(request.content());
        if (content.isBlank()) {
            throw new ResponseStatusException(HttpStatus.BAD_REQUEST, "资产正文不能为空");
        }
        Instant now = Instant.now();
        String usagePolicy = normalizeUsagePolicy(request.usagePolicy());
        SecureAsset asset = new SecureAsset(
                UUID.randomUUID().toString(),
                normalizeUserId(userId),
                request.name().trim(),
                normalizeType(request.type()),
                normalizeText(request.description()),
                normalizeContentType(request.contentType()),
                cryptoService.encrypt(content),
                usagePolicy,
                normalizeRemainingUses(usagePolicy, request.remainingUses()),
                request.enabled() == null || request.enabled(),
                now,
                now,
                null
        );
        return toResponse(repository.save(asset));
    }

    /** 更新资产元数据；content 为 null 时保留旧密文，避免编辑页面回传敏感正文。 */
    public SecureAssetResponse updateAsset(String userId, String assetId, SecureAssetUpsertRequest request) {
        SecureAsset existing = findOwnedAsset(userId, assetId);
        String usagePolicy = normalizeUsagePolicy(request.usagePolicy());
        String payloadCiphertext = request.content() == null
                ? existing.payloadCiphertext()
                : encryptUpdatedContent(request.content());
        SecureAsset updated = new SecureAsset(
                existing.id(),
                existing.userId(),
                request.name().trim(),
                normalizeType(request.type()),
                normalizeText(request.description()),
                normalizeContentType(request.contentType()),
                payloadCiphertext,
                usagePolicy,
                normalizeRemainingUsesForUpdate(existing, usagePolicy, request.remainingUses()),
                request.enabled() == null || request.enabled(),
                existing.createdAt(),
                Instant.now(),
                existing.lastUsedAt()
        );
        return toResponse(repository.save(updated));
    }

    /** 删除当前用户拥有的资产；跨用户 ID 会按不存在处理。 */
    public void deleteAsset(String userId, String assetId) {
        if (repository.deleteByIdAndUserId(normalizeAssetId(assetId), normalizeUserId(userId)) == 0) {
            throw new ResponseStatusException(HttpStatus.NOT_FOUND, "安全资产不存在");
        }
    }

    /**
     * 为受信任 Runtime 解析资产正文。
     *
     * <p>一次性资产在同一事务内先原子扣减库存，再返回明文；库存竞争失败时不会交付正文。</p>
     */
    @Transactional
    public SecureAssetRuntimeResponse resolveForRuntime(String userId, String assetId) {
        String normalizedUserId = normalizeUserId(userId);
        SecureAsset asset = findOwnedAsset(normalizedUserId, assetId);
        if (!asset.enabled()) {
            throw new ResponseStatusException(HttpStatus.CONFLICT, "安全资产已停用");
        }

        Instant resolvedAt = Instant.now();
        Integer remainingUses = asset.remainingUses();
        if (SINGLE_USE.equals(asset.usagePolicy())) {
            if (repository.consumeSingleUseAsset(asset.id(), normalizedUserId, resolvedAt) == 0) {
                throw new ResponseStatusException(HttpStatus.CONFLICT, "一次性资产库存不足或已被消费");
            }
            remainingUses = Math.max((remainingUses == null ? 0 : remainingUses) - 1, 0);
        } else if (repository.touchReusableAsset(asset.id(), normalizedUserId, resolvedAt) == 0) {
            throw new ResponseStatusException(HttpStatus.CONFLICT, "安全资产当前不可用");
        }

        return new SecureAssetRuntimeResponse(
                asset.id(),
                asset.name(),
                asset.type(),
                asset.description(),
                asset.contentType(),
                cryptoService.decrypt(asset.payloadCiphertext()),
                asset.usagePolicy(),
                remainingUses,
                resolvedAt
        );
    }

    /** 查询并校验资产所有权，避免调用方遗漏 userId 条件。 */
    private SecureAsset findOwnedAsset(String userId, String assetId) {
        return repository.findByIdAndUserId(normalizeAssetId(assetId), normalizeUserId(userId))
                .orElseThrow(() -> new ResponseStatusException(HttpStatus.NOT_FOUND, "安全资产不存在"));
    }

    /** 把内部资产转换成不包含敏感正文的桌面端响应。 */
    private SecureAssetResponse toResponse(SecureAsset asset) {
        return new SecureAssetResponse(
                asset.id(), asset.name(), asset.type(), asset.description(), asset.contentType(),
                asset.usagePolicy(), asset.remainingUses(), asset.enabled(),
                asset.payloadCiphertext() != null && !asset.payloadCiphertext().isBlank(),
                asset.createdAt(), asset.updatedAt(), asset.lastUsedAt()
        );
    }

    /** 更新正文时拒绝空内容，防止误操作把仍被 Profile 引用的资产清空。 */
    private String encryptUpdatedContent(String content) {
        String normalized = normalizeContent(content);
        if (normalized.isBlank()) {
            throw new ResponseStatusException(HttpStatus.BAD_REQUEST, "资产正文不能为空");
        }
        return cryptoService.encrypt(normalized);
    }

    /** 校验用户 ID，所有资产操作都必须明确归属用户。 */
    private String normalizeUserId(String userId) {
        String normalized = normalizeText(userId);
        if (normalized.isBlank()) {
            throw new ResponseStatusException(HttpStatus.BAD_REQUEST, "用户标识不能为空");
        }
        return normalized;
    }

    /** 校验资产 ID，避免空路径参数进入仓储层。 */
    private String normalizeAssetId(String assetId) {
        String normalized = normalizeText(assetId);
        if (normalized.isBlank()) {
            throw new ResponseStatusException(HttpStatus.BAD_REQUEST, "资产标识不能为空");
        }
        return normalized;
    }

    /** 规范资产类型，便于前端和工具层稳定判断。 */
    private String normalizeType(String type) {
        return normalizeText(type).toUpperCase(Locale.ROOT);
    }

    /** 规范使用策略，只接受可复用和一次性库存两种明确语义。 */
    private String normalizeUsagePolicy(String usagePolicy) {
        String normalized = normalizeText(usagePolicy).toUpperCase(Locale.ROOT);
        if (normalized.isBlank()) {
            return REUSABLE;
        }
        if (!REUSABLE.equals(normalized) && !SINGLE_USE.equals(normalized)) {
            throw new ResponseStatusException(HttpStatus.BAD_REQUEST, "usagePolicy 仅支持 REUSABLE 或 SINGLE_USE");
        }
        return normalized;
    }

    /** 为一次性资产校验库存，可复用资产固定不保存剩余次数。 */
    private Integer normalizeRemainingUses(String usagePolicy, Integer remainingUses) {
        if (REUSABLE.equals(usagePolicy)) {
            return null;
        }
        int normalized = remainingUses == null ? 1 : remainingUses;
        if (normalized < 0) {
            throw new ResponseStatusException(HttpStatus.BAD_REQUEST, "一次性资产库存不能小于 0");
        }
        return normalized;
    }

    /**
     * 规范更新场景的一次性库存。
     *
     * <p>桌面端编辑名称、说明等元数据时可以不提交 remainingUses。此时如果资产仍然是
     * SINGLE_USE，就沿用数据库中的剩余次数，避免把已经消费过的库存意外重置为 1。
     * 从 REUSABLE 切换为 SINGLE_USE 时没有历史库存，因此仍使用创建场景的默认值 1。</p>
     */
    private Integer normalizeRemainingUsesForUpdate(
            SecureAsset existing,
            String usagePolicy,
            Integer requestedRemainingUses
    ) {
        if (REUSABLE.equals(usagePolicy)) {
            return null;
        }
        if (requestedRemainingUses == null && SINGLE_USE.equals(existing.usagePolicy())) {
            return normalizeRemainingUses(usagePolicy, existing.remainingUses());
        }
        return normalizeRemainingUses(usagePolicy, requestedRemainingUses);
    }

    /** 缺省 MIME 类型按纯文本处理，图片可使用 data URL 或 Base64 正文。 */
    private String normalizeContentType(String contentType) {
        String normalized = normalizeText(contentType);
        return normalized.isBlank() ? "text/plain" : normalized;
    }

    /** 普通元数据去除首尾空白。 */
    private String normalizeText(String value) {
        return value == null ? "" : value.trim();
    }

    /** 资产正文只去除首尾空白，不改写内部换行或编码内容。 */
    private String normalizeContent(String value) {
        return value == null ? "" : value.strip();
    }
}
