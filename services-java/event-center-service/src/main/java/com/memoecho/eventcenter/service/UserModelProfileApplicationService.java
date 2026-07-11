package com.memoecho.eventcenter.service;

import com.memoecho.eventcenter.dto.UserModelProfileResolveRequest;
import com.memoecho.eventcenter.dto.UserModelProfileResolveResponse;
import com.memoecho.eventcenter.dto.UserModelProfileResolvedResponse;
import com.memoecho.eventcenter.dto.UserModelProfileResponse;
import com.memoecho.eventcenter.dto.UserModelProfileUpsertRequest;
import com.memoecho.eventcenter.model.UserModelProfile;
import com.memoecho.eventcenter.repository.UserModelProfileRepository;
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
public class UserModelProfileApplicationService {

    private static final String DEFAULT_PROVIDER = "OPENAI_COMPATIBLE";

    private final UserModelProfileRepository repository;
    private final ApiKeyCryptoService apiKeyCryptoService;

    public UserModelProfileApplicationService(
            UserModelProfileRepository repository,
            ApiKeyCryptoService apiKeyCryptoService
    ) {
        /**
         * 这个构造函数的作用是注入用户模型配置仓储和 API Key 加解密服务，
         * 后续创建、更新、展示、运行时解析都从这里统一走。
         */
        this.repository = repository;
        this.apiKeyCryptoService = apiKeyCryptoService;
    }

    /**
     * 这个函数的作用是返回当前全部用户模型配置，供前端配置中心直接展示。
     */
    public List<UserModelProfileResponse> listProfiles() {
        return repository.findAll().stream()
                .map(this::toResponse)
                .toList();
    }

    /**
     * 返回指定用户自己的模型配置，供已登录工作台使用。
     */
    public List<UserModelProfileResponse> listProfiles(String userId) {
        return repository.findAllByUserId(normalizeUserId(userId)).stream()
                .map(this::toResponse)
                .toList();
    }

    /**
     * 这个函数的作用是按 id 查询单条用户模型配置，不存在时返回 404。
     */
    public UserModelProfileResponse getProfile(String profileId) {
        return repository.findById(profileId)
                .map(this::toResponse)
                .orElseThrow(() -> new ResponseStatusException(HttpStatus.NOT_FOUND, "用户模型配置不存在"));
    }

    /**
     * 查询当前用户拥有的一条模型配置，避免通过配置 id 越权读取。
     */
    public UserModelProfileResponse getProfile(String userId, String profileId) {
        return findOwnedProfile(userId, profileId)
                .map(this::toResponse)
                .orElseThrow(() -> new ResponseStatusException(HttpStatus.NOT_FOUND, "用户模型配置不存在"));
    }

    /**
     * 这个函数的作用是创建新的用户模型配置，并在落库前把 API Key 加密。
     */
    public UserModelProfileResponse createProfile(UserModelProfileUpsertRequest request) {
        return createProfile(request.userId(), request);
    }

    /**
     * 为指定登录用户创建模型配置；请求体中的 userId 不参与归属判定。
     */
    public UserModelProfileResponse createProfile(String userId, UserModelProfileUpsertRequest request) {
        Instant now = Instant.now();
        UserModelProfile profile = new UserModelProfile(
                UUID.randomUUID().toString(),
                normalizeUserId(userId),
                request.name().trim(),
                normalizeText(request.description()),
                request.enabled() == null || request.enabled(),
                normalizeProvider(request.provider()),
                normalizeText(request.baseUrl()),
                encryptSecret(request.apiKey()),
                normalizeText(request.model()),
                normalizeTemperature(request.temperature()),
                normalizeMaxTokens(request.maxTokens()),
                normalizeRoutes(request.supportedRoutes()),
                request.isDefault() != null && request.isDefault(),
                normalizePriority(request.priority()),
                now,
                now
        );
        if (profile.isDefault()) {
            clearDefaultForSameUser(profile.userId(), profile.id());
        }
        return toResponse(repository.save(profile));
    }

    /**
     * 这个函数的作用是更新已有用户模型配置，并支持保留、覆盖或清空 API Key。
     */
    public UserModelProfileResponse updateProfile(String profileId, UserModelProfileUpsertRequest request) {
        UserModelProfile existing = repository.findById(profileId)
                .orElseThrow(() -> new ResponseStatusException(HttpStatus.NOT_FOUND, "用户模型配置不存在"));

        return updateProfile(existing.userId(), profileId, request);
    }

    /**
     * 更新当前用户拥有的配置，并始终保留原有归属，防止请求体篡改 userId。
     */
    public UserModelProfileResponse updateProfile(
            String userId,
            String profileId,
            UserModelProfileUpsertRequest request
    ) {
        UserModelProfile existing = findOwnedProfile(userId, profileId)
                .orElseThrow(() -> new ResponseStatusException(HttpStatus.NOT_FOUND, "用户模型配置不存在"));

        UserModelProfile updated = new UserModelProfile(
                existing.id(),
                existing.userId(),
                request.name().trim(),
                normalizeText(request.description()),
                request.enabled() == null || request.enabled(),
                normalizeProvider(request.provider()),
                normalizeText(request.baseUrl()),
                resolveUpdatedApiKey(existing.apiKey(), request.apiKey(), request.clearApiKey()),
                normalizeText(request.model()),
                normalizeTemperature(request.temperature()),
                normalizeMaxTokens(request.maxTokens()),
                normalizeRoutes(request.supportedRoutes()),
                request.isDefault() != null && request.isDefault(),
                normalizePriority(request.priority()),
                existing.createdAt(),
                Instant.now()
        );
        if (updated.isDefault()) {
            clearDefaultForSameUser(updated.userId(), updated.id());
        }
        return toResponse(repository.save(updated));
    }

    /**
     * 这个函数的作用是删除指定的用户模型配置。
     */
    public void deleteProfile(String profileId) {
        repository.deleteById(profileId);
    }

    /**
     * 删除当前用户拥有的配置；不属于当前用户时按不存在处理，避免泄露资源归属。
     */
    public void deleteProfile(String userId, String profileId) {
        if (findOwnedProfile(userId, profileId).isEmpty()) {
            throw new ResponseStatusException(HttpStatus.NOT_FOUND, "用户模型配置不存在");
        }
        repository.deleteByIdAndUserId(profileId, normalizeUserId(userId));
    }

    /**
     * 这个函数的作用是根据 userId 和 route 解析当前最适合运行时使用的模型配置。
     */
    public UserModelProfileResolveResponse resolveProfile(UserModelProfileResolveRequest request) {
        return resolveProfile(request.userId(), request);
    }

    /**
     * 在指定用户范围内为运行时 route 解析可用模型，显式绑定也必须属于该用户。
     */
    public UserModelProfileResolveResponse resolveProfile(String userId, UserModelProfileResolveRequest request) {
        String normalizedUserId = normalizeUserId(userId);
        String explicitProfileId = normalizeText(request.profileId());
        if (!explicitProfileId.isBlank()) {
            Optional<UserModelProfile> explicitProfile = repository.findByIdAndUserId(explicitProfileId, normalizedUserId)
                    .filter(UserModelProfile::enabled);
            if (explicitProfile.isPresent()) {
                return new UserModelProfileResolveResponse(
                        true,
                        "命中会话显式绑定模型配置",
                        toResolvedResponse(explicitProfile.get())
                );
            }
            return new UserModelProfileResolveResponse(false, "显式绑定的模型配置不存在或不可用", null);
        }

        Optional<UserModelProfile> bestProfile = repository.findAllByUserId(normalizedUserId).stream()
                .filter(UserModelProfile::enabled)
                .filter(profile -> matchesRoute(profile.supportedRoutes(), request.route()))
                .max(Comparator
                        .comparingInt(UserModelProfile::priority)
                        .thenComparingInt(this::routeSpecificityScore)
                        .thenComparingInt(profile -> profile.isDefault() ? 1 : 0)
                        .thenComparing(UserModelProfile::updatedAt));

        if (bestProfile.isEmpty()) {
            return new UserModelProfileResolveResponse(false, "未命中任何用户模型配置", null);
        }

        UserModelProfile profile = bestProfile.get();
        String reason = profile.supportedRoutes().isEmpty()
                ? "命中用户默认模型配置"
                : "命中 route 定向模型配置";
        return new UserModelProfileResolveResponse(true, reason, toResolvedResponse(profile));
    }

    /**
     * 这个函数的作用是保证同一个用户同一时刻只有一条默认模型配置。
     */
    private void clearDefaultForSameUser(String userId, String keepProfileId) {
        repository.findAllByUserId(userId).stream()
                .filter(UserModelProfile::isDefault)
                .filter(profile -> !profile.id().equals(keepProfileId))
                .forEach(profile -> repository.save(new UserModelProfile(
                        profile.id(),
                        profile.userId(),
                        profile.name(),
                        profile.description(),
                        profile.enabled(),
                        profile.provider(),
                        profile.baseUrl(),
                        profile.apiKey(),
                        profile.model(),
                        profile.temperature(),
                        profile.maxTokens(),
                        profile.supportedRoutes(),
                        false,
                        profile.priority(),
                        profile.createdAt(),
                        Instant.now()
                )));
    }

    /**
     * 在仓储查询前规范化用户标识，保证所有权判断与持久化值使用同一格式。
     */
    private String normalizeUserId(String userId) {
        String normalized = normalizeText(userId);
        if (normalized.isBlank()) {
            throw new ResponseStatusException(HttpStatus.BAD_REQUEST, "用户标识不能为空");
        }
        return normalized;
    }

    /**
     * 查询指定用户拥有的配置；调用方可将空结果统一映射为 404。
     */
    private Optional<UserModelProfile> findOwnedProfile(String userId, String profileId) {
        return repository.findByIdAndUserId(profileId, normalizeUserId(userId));
    }

    /**
     * 这个函数的作用是统一处理模型配置对 route 的匹配规则，空列表表示全量适配。
     */
    private boolean matchesRoute(List<String> supportedRoutes, String route) {
        if (supportedRoutes == null || supportedRoutes.isEmpty()) {
            return true;
        }
        String normalizedRoute = normalizeText(route).toLowerCase(Locale.ROOT);
        return supportedRoutes.stream().anyMatch(item -> item.equalsIgnoreCase(normalizedRoute));
    }

    /**
     * 这个函数的作用是在优先级相同的情况下，让显式绑定 route 的配置优先命中。
     */
    private int routeSpecificityScore(UserModelProfile profile) {
        return profile.supportedRoutes().isEmpty() ? 0 : 1;
    }

    /**
     * 这个函数的作用是把内部配置对象转换成前端展示用结构，并对密钥做脱敏。
     */
    private UserModelProfileResponse toResponse(UserModelProfile profile) {
        String apiKey = decryptSecret(profile.apiKey());
        return new UserModelProfileResponse(
                profile.id(),
                profile.userId(),
                profile.name(),
                profile.description(),
                profile.enabled(),
                profile.provider(),
                profile.baseUrl(),
                !apiKey.isBlank(),
                maskSecret(apiKey),
                profile.model(),
                profile.temperature(),
                profile.maxTokens(),
                profile.supportedRoutes(),
                profile.isDefault(),
                profile.priority(),
                profile.createdAt(),
                profile.updatedAt()
        );
    }

    /**
     * 这个函数的作用是生成运行时专用的完整模型配置，其中会返回解密后的真实 API Key。
     */
    private UserModelProfileResolvedResponse toResolvedResponse(UserModelProfile profile) {
        return new UserModelProfileResolvedResponse(
                profile.id(),
                profile.userId(),
                profile.name(),
                profile.provider(),
                profile.baseUrl(),
                decryptSecret(profile.apiKey()),
                profile.model(),
                profile.temperature(),
                profile.maxTokens(),
                profile.supportedRoutes(),
                profile.isDefault(),
                profile.priority()
        );
    }

    /**
     * 这个函数的作用是合并更新请求和旧配置中的密钥值，支持保留、覆盖和清空三种情况。
     */
    private String resolveUpdatedApiKey(String existingApiKey, String requestApiKey, Boolean clearApiKey) {
        if (Boolean.TRUE.equals(clearApiKey)) {
            return "";
        }
        if (requestApiKey != null) {
            return encryptSecret(requestApiKey);
        }
        return normalizeSecret(existingApiKey);
    }

    /**
     * 这个函数的作用是清洗支持的 route 列表，统一成去重后的非空小写值。
     */
    private List<String> normalizeRoutes(List<String> routes) {
        if (routes == null) {
            return List.of();
        }
        return routes.stream()
                .filter(item -> item != null && !item.isBlank())
                .map(item -> item.trim().toLowerCase(Locale.ROOT))
                .distinct()
                .toList();
    }

    /**
     * 这个函数的作用是统一 provider 的默认值，避免前端未填写时后端出现空语义。
     */
    private String normalizeProvider(String provider) {
        String normalized = normalizeText(provider).toUpperCase(Locale.ROOT);
        return normalized.isBlank() ? DEFAULT_PROVIDER : normalized;
    }

    /**
     * 这个函数的作用是把可选文本字段统一整理成非 null 字符串。
     */
    private String normalizeText(String value) {
        return value == null ? "" : value.trim();
    }

    /**
     * 这个函数的作用是统一清理密钥字段首尾空格，避免运行时携带无效空白字符。
     */
    private String normalizeSecret(String value) {
        return normalizeText(value);
    }

    /**
     * 这个函数的作用是把输入的明文 API Key 清洗后再加密，保证落库前不出现明文。
     */
    private String encryptSecret(String value) {
        String normalized = normalizeSecret(value);
        if (normalized.isBlank()) {
            return "";
        }
        return apiKeyCryptoService.encrypt(normalized);
    }

    /**
     * 这个函数的作用是把数据库中的密文 API Key 解密成明文，并兼容历史明文数据。
     */
    private String decryptSecret(String value) {
        return apiKeyCryptoService.decrypt(normalizeSecret(value));
    }

    /**
     * 这个函数的作用是把 temperature 约束到安全区间，便于后续直接透传给模型接口。
     */
    private Double normalizeTemperature(Double value) {
        if (value == null) {
            return null;
        }
        if (value < 0) {
            return 0.0;
        }
        if (value > 2) {
            return 2.0;
        }
        return value;
    }

    /**
     * 这个函数的作用是保证 maxTokens 至少是正整数。
     */
    private Integer normalizeMaxTokens(Integer value) {
        if (value == null) {
            return null;
        }
        return Math.max(value, 1);
    }

    /**
     * 这个函数的作用是统一优先级默认值，便于多条配置共存时稳定排序。
     */
    private int normalizePriority(Integer value) {
        return value == null ? 0 : value;
    }

    /**
     * 这个函数的作用是把敏感密钥脱敏后返回给前端，避免页面直接泄露完整 key。
     */
    private String maskSecret(String secret) {
        if (secret == null || secret.isBlank()) {
            return "";
        }
        if (secret.length() <= 8) {
            return "****";
        }
        return secret.substring(0, 4) + "****" + secret.substring(secret.length() - 4);
    }
}
