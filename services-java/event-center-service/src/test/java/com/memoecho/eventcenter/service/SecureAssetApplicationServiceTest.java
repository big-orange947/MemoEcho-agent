package com.memoecho.eventcenter.service;

import com.memoecho.eventcenter.config.EventCenterSecurityProperties;
import com.memoecho.eventcenter.dto.SecureAssetResponse;
import com.memoecho.eventcenter.dto.SecureAssetRuntimeResponse;
import com.memoecho.eventcenter.dto.SecureAssetUpsertRequest;
import com.memoecho.eventcenter.model.SecureAsset;
import com.memoecho.eventcenter.repository.SecureAssetRepository;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.extension.ExtendWith;
import org.mockito.ArgumentCaptor;
import org.mockito.Mock;
import org.mockito.junit.jupiter.MockitoExtension;
import org.springframework.web.server.ResponseStatusException;

import java.time.Instant;
import java.util.Optional;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertFalse;
import static org.junit.jupiter.api.Assertions.assertNotEquals;
import static org.junit.jupiter.api.Assertions.assertThrows;
import static org.junit.jupiter.api.Assertions.assertTrue;
import static org.mockito.ArgumentMatchers.any;
import static org.mockito.Mockito.verify;
import static org.mockito.Mockito.when;

/** 验证安全资产应用层的加密、脱敏、所有权和一次性库存规则。 */
@ExtendWith(MockitoExtension.class)
class SecureAssetApplicationServiceTest {

    @Mock
    private SecureAssetRepository repository;

    private AssetPayloadCryptoService cryptoService;
    private SecureAssetApplicationService applicationService;

    /** 每个测试使用独立主密钥构造真实 AES-GCM 服务，避免只验证 Mock 行为。 */
    @BeforeEach
    void setUp() {
        EventCenterSecurityProperties properties = new EventCenterSecurityProperties();
        properties.setApiKeySecret("secure-asset-unit-test-secret");
        cryptoService = new AssetPayloadCryptoService(new ApiKeyCryptoService(properties));
        applicationService = new SecureAssetApplicationService(repository, cryptoService);
    }

    /** 创建资产时必须加密正文，普通响应只暴露 contentConfigured。 */
    @Test
    void shouldEncryptPayloadAndReturnMetadataOnly() {
        when(repository.save(any(SecureAsset.class))).thenAnswer(invocation -> invocation.getArgument(0));

        SecureAssetResponse response = applicationService.createAsset("freeze", new SecureAssetUpsertRequest(
                "微信收款码", "PAYMENT_CODE", "成交后发送", "image/png",
                "data:image/png;base64,secret-image", "REUSABLE", null, true
        ));

        ArgumentCaptor<SecureAsset> captor = ArgumentCaptor.forClass(SecureAsset.class);
        verify(repository).save(captor.capture());
        SecureAsset stored = captor.getValue();
        assertNotEquals("data:image/png;base64,secret-image", stored.payloadCiphertext());
        assertEquals("data:image/png;base64,secret-image", cryptoService.decrypt(stored.payloadCiphertext()));
        assertTrue(response.contentConfigured());
        assertEquals("PAYMENT_CODE", response.type());
    }

    /** 编辑一次性资产的元数据时，省略 remainingUses 必须保留已经扣减后的库存。 */
    @Test
    void shouldPreserveSingleUseInventoryWhenUpdateOmitsRemainingUses() {
        SecureAsset existing = asset("SINGLE_USE", 7, true);
        when(repository.findByIdAndUserId("asset-1", "freeze")).thenReturn(Optional.of(existing));
        when(repository.save(any(SecureAsset.class))).thenAnswer(invocation -> invocation.getArgument(0));

        applicationService.updateAsset("freeze", "asset-1", new SecureAssetUpsertRequest(
                "新名称", "LICENSE_CODE", "更新后的说明", "text/plain",
                null, "SINGLE_USE", null, true
        ));

        ArgumentCaptor<SecureAsset> captor = ArgumentCaptor.forClass(SecureAsset.class);
        verify(repository).save(captor.capture());
        assertEquals(7, captor.getValue().remainingUses());
        assertEquals(existing.payloadCiphertext(), captor.getValue().payloadCiphertext());
    }

    /** 一次性资产必须先成功扣减库存，随后才允许把明文返回给 Runtime。 */
    @Test
    void shouldConsumeSingleUseAssetBeforeResolvingContent() {
        SecureAsset asset = asset("SINGLE_USE", 2, true);
        when(repository.findByIdAndUserId("asset-1", "freeze")).thenReturn(Optional.of(asset));
        when(repository.consumeSingleUseAsset(any(), any(), any())).thenReturn(1);

        SecureAssetRuntimeResponse response = applicationService.resolveForRuntime("freeze", "asset-1");

        assertEquals("CARD-SECRET-001", response.content());
        assertEquals(1, response.remainingUses());
        verify(repository).consumeSingleUseAsset(any(), any(), any());
    }

    /** 并发竞争导致库存扣减失败时必须拒绝交付正文。 */
    @Test
    void shouldRejectSingleUseAssetWhenAtomicClaimFails() {
        SecureAsset asset = asset("SINGLE_USE", 1, true);
        when(repository.findByIdAndUserId("asset-1", "freeze")).thenReturn(Optional.of(asset));
        when(repository.consumeSingleUseAsset(any(), any(), any())).thenReturn(0);

        assertThrows(ResponseStatusException.class,
                () -> applicationService.resolveForRuntime("freeze", "asset-1"));
    }

    /** 停用资产即使仍有正文也不能被 Runtime 解析。 */
    @Test
    void shouldRejectDisabledAsset() {
        when(repository.findByIdAndUserId("asset-1", "freeze"))
                .thenReturn(Optional.of(asset("REUSABLE", null, false)));

        assertThrows(ResponseStatusException.class,
                () -> applicationService.resolveForRuntime("freeze", "asset-1"));
    }

    /** 构造携带真实密文的测试资产。 */
    private SecureAsset asset(String usagePolicy, Integer remainingUses, boolean enabled) {
        return new SecureAsset(
                "asset-1", "freeze", "卡密", "LICENSE_CODE", "付款后交付", "text/plain",
                cryptoService.encrypt("CARD-SECRET-001"), usagePolicy, remainingUses, enabled,
                Instant.parse("2026-07-17T00:00:00Z"), Instant.parse("2026-07-17T00:00:00Z"), null
        );
    }
}
