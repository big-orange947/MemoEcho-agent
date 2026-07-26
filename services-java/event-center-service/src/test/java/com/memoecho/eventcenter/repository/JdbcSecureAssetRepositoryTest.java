package com.memoecho.eventcenter.repository;

import com.memoecho.eventcenter.model.SecureAsset;
import org.junit.jupiter.api.Test;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.boot.test.autoconfigure.jdbc.JdbcTest;
import org.springframework.context.annotation.Import;
import org.springframework.test.context.TestPropertySource;

import java.time.Instant;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertTrue;

/** 验证安全资产 JDBC 仓储在 H2 与 MySQL 兼容 SQL 下的核心行为。 */
@JdbcTest
@Import(JdbcSecureAssetRepository.class)
@TestPropertySource(properties = "spring.sql.init.mode=always")
class JdbcSecureAssetRepositoryTest {

    @Autowired
    private JdbcSecureAssetRepository repository;

    /** 保存后只能通过相同 userId 查询到资产。 */
    @Test
    void shouldPersistAssetWithOwnershipBoundary() {
        repository.save(asset("asset-1", "freeze", "REUSABLE", null));

        assertTrue(repository.findByIdAndUserId("asset-1", "freeze").isPresent());
        assertTrue(repository.findByIdAndUserId("asset-1", "another-user").isEmpty());
        assertEquals(1, repository.findAllByUserId("freeze").size());
    }

    /** 一次性库存只能扣减到零，不能产生负库存。 */
    @Test
    void shouldAtomicallyConsumeSingleUseInventory() {
        repository.save(asset("asset-2", "freeze", "SINGLE_USE", 1));
        Instant usedAt = Instant.parse("2026-07-17T01:00:00Z");

        assertEquals(1, repository.consumeSingleUseAsset("asset-2", "freeze", usedAt));
        assertEquals(0, repository.consumeSingleUseAsset("asset-2", "freeze", usedAt));
        assertEquals(0, repository.findByIdAndUserId("asset-2", "freeze").orElseThrow().remainingUses());
    }

    /** 构造仓储测试资产，payloadCiphertext 使用假密文以聚焦 SQL 行为。 */
    private SecureAsset asset(String id, String userId, String usagePolicy, Integer remainingUses) {
        Instant now = Instant.parse("2026-07-17T00:00:00Z");
        return new SecureAsset(
                id, userId, "测试资产", "TEXT_SECRET", "", "text/plain", "enc::test",
                usagePolicy, remainingUses, true, now, now, null
        );
    }
}
