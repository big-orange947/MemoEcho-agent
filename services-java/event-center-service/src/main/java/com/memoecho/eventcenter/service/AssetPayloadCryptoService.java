package com.memoecho.eventcenter.service;

import org.springframework.stereotype.Service;

/**
 * 为安全资产提供语义明确的加解密入口。
 *
 * <p>底层复用项目已经验证过的 AES-GCM 实现和主密钥，避免维护第二套密码学代码。</p>
 */
@Service
public class AssetPayloadCryptoService {

    private final ApiKeyCryptoService cryptoService;

    /** 注入现有 AES-GCM 服务，资产层不直接接触密钥配置。 */
    public AssetPayloadCryptoService(ApiKeyCryptoService cryptoService) {
        this.cryptoService = cryptoService;
    }

    /** 在资产正文写入数据库前完成加密。 */
    public String encrypt(String content) {
        return cryptoService.encrypt(content);
    }

    /** 仅在 Runtime 已通过身份校验后解密资产正文。 */
    public String decrypt(String ciphertext) {
        return cryptoService.decrypt(ciphertext);
    }
}
