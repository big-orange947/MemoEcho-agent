package com.memoecho.eventcenter.service;

import com.memoecho.eventcenter.config.DownstreamServiceProperties;
import com.memoecho.eventcenter.dto.PlatformConnectionResponse;
import com.memoecho.eventcenter.dto.PlatformConnectionUpsertRequest;
import com.memoecho.eventcenter.model.PlatformConnection;
import com.memoecho.eventcenter.repository.PlatformConnectionRepository;
import org.junit.jupiter.api.Test;
import org.mockito.ArgumentCaptor;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertTrue;
import static org.mockito.BDDMockito.given;
import static org.mockito.Mockito.mock;
import static org.mockito.Mockito.verify;

class PlatformConnectionApplicationServiceTest {

    @Test
    void shouldEncryptCredentialAndBindConnectionToCurrentUser() {
        // 这个测试函数的作用是验证明文凭据不会直接进入仓储，并且连接记录带有当前用户所有权。
        PlatformConnectionRepository repository = mock(PlatformConnectionRepository.class);
        ApiKeyCryptoService cryptoService = mock(ApiKeyCryptoService.class);
        DownstreamServiceProperties properties = new DownstreamServiceProperties();
        PlatformConnectionApplicationService service = new PlatformConnectionApplicationService(
                repository, cryptoService, properties);
        given(cryptoService.encrypt("secret-token")).willReturn("enc::ciphertext");
        given(repository.save(org.mockito.ArgumentMatchers.any())).willAnswer(invocation -> invocation.getArgument(0));

        PlatformConnectionResponse response = service.create("user-001", new PlatformConnectionUpsertRequest(
                "我的 QQ", "QQ", "NapCat", true, "http://127.0.0.1:8091/", "secret-token"));

        ArgumentCaptor<PlatformConnection> captor = ArgumentCaptor.forClass(PlatformConnection.class);
        verify(repository).save(captor.capture());
        assertEquals("user-001", captor.getValue().userId());
        assertEquals("enc::ciphertext", captor.getValue().credentialCiphertext());
        assertEquals("http://127.0.0.1:8091", captor.getValue().connectorBaseUrl());
        assertTrue(response.hasCredential());
    }
}
