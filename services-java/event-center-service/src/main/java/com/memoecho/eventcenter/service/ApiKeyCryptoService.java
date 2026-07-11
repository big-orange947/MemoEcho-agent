package com.memoecho.eventcenter.service;

import com.memoecho.eventcenter.config.EventCenterSecurityProperties;
import org.springframework.stereotype.Service;

import javax.crypto.Cipher;
import javax.crypto.spec.GCMParameterSpec;
import javax.crypto.spec.SecretKeySpec;
import java.nio.charset.StandardCharsets;
import java.security.MessageDigest;
import java.security.SecureRandom;
import java.util.Base64;

@Service
public class ApiKeyCryptoService {

    private static final String PREFIX = "enc::";
    private static final int NONCE_LENGTH = 12;
    private static final int TAG_LENGTH_BIT = 128;
    private static final String ALGORITHM = "AES/GCM/NoPadding";

    private final SecretKeySpec secretKeySpec;
    private final SecureRandom secureRandom = new SecureRandom();

    public ApiKeyCryptoService(EventCenterSecurityProperties securityProperties) {
        /**
         * 这个构造函数的作用是根据配置里的主密钥派生出固定长度的 AES 密钥，
         * 后续所有 API Key 的落库加密和运行时解密都复用这把密钥。
         */
        this.secretKeySpec = new SecretKeySpec(deriveKeyBytes(securityProperties.getApiKeySecret()), "AES");
    }

    /**
     * 这个函数的作用是把明文 API Key 加密成可安全落库的密文字符串。
     */
    public String encrypt(String plainText) {
        if (plainText == null || plainText.isBlank()) {
            return "";
        }
        if (isEncrypted(plainText)) {
            return plainText;
        }

        try {
            byte[] nonce = new byte[NONCE_LENGTH];
            secureRandom.nextBytes(nonce);

            Cipher cipher = Cipher.getInstance(ALGORITHM);
            cipher.init(Cipher.ENCRYPT_MODE, secretKeySpec, new GCMParameterSpec(TAG_LENGTH_BIT, nonce));
            byte[] cipherBytes = cipher.doFinal(plainText.getBytes(StandardCharsets.UTF_8));

            byte[] merged = new byte[nonce.length + cipherBytes.length];
            System.arraycopy(nonce, 0, merged, 0, nonce.length);
            System.arraycopy(cipherBytes, 0, merged, nonce.length, cipherBytes.length);
            return PREFIX + Base64.getEncoder().encodeToString(merged);
        } catch (Exception exc) {
            throw new IllegalStateException("Failed to encrypt api key", exc);
        }
    }

    /**
     * 这个函数的作用是把数据库中的密文 API Key 解密成明文。
     * 如果读到的是历史明文数据，会原样返回，方便平滑迁移。
     */
    public String decrypt(String cipherText) {
        if (cipherText == null || cipherText.isBlank()) {
            return "";
        }
        if (!isEncrypted(cipherText)) {
            return cipherText;
        }

        try {
            byte[] merged = Base64.getDecoder().decode(cipherText.substring(PREFIX.length()));
            if (merged.length <= NONCE_LENGTH) {
                throw new IllegalStateException("Encrypted api key payload is invalid");
            }

            byte[] nonce = new byte[NONCE_LENGTH];
            byte[] cipherBytes = new byte[merged.length - NONCE_LENGTH];
            System.arraycopy(merged, 0, nonce, 0, NONCE_LENGTH);
            System.arraycopy(merged, NONCE_LENGTH, cipherBytes, 0, cipherBytes.length);

            Cipher cipher = Cipher.getInstance(ALGORITHM);
            cipher.init(Cipher.DECRYPT_MODE, secretKeySpec, new GCMParameterSpec(TAG_LENGTH_BIT, nonce));
            byte[] plainBytes = cipher.doFinal(cipherBytes);
            return new String(plainBytes, StandardCharsets.UTF_8);
        } catch (Exception exc) {
            throw new IllegalStateException("Failed to decrypt api key", exc);
        }
    }

    /**
     * 这个函数的作用是判断当前字符串是否已经是本服务加密后的密文。
     */
    public boolean isEncrypted(String value) {
        return value != null && value.startsWith(PREFIX);
    }

    /**
     * 这个函数的作用是把配置主密钥派生为 256 位 AES 密钥字节。
     */
    private byte[] deriveKeyBytes(String secret) {
        try {
            MessageDigest digest = MessageDigest.getInstance("SHA-256");
            return digest.digest((secret == null ? "" : secret).getBytes(StandardCharsets.UTF_8));
        } catch (Exception exc) {
            throw new IllegalStateException("Failed to derive crypto key", exc);
        }
    }
}
