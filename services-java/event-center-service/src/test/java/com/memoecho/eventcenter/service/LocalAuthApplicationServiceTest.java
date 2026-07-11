package com.memoecho.eventcenter.service;

import com.memoecho.eventcenter.dto.AuthTokenResponse;
import com.memoecho.eventcenter.dto.UserLoginRequest;
import com.memoecho.eventcenter.dto.UserRegisterRequest;
import com.memoecho.eventcenter.model.LocalUser;
import com.memoecho.eventcenter.repository.LocalUserRepository;
import org.junit.jupiter.api.Test;
import org.mockito.ArgumentCaptor;

import java.util.Optional;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertFalse;
import static org.mockito.BDDMockito.given;
import static org.mockito.Mockito.mock;
import static org.mockito.Mockito.verify;

class LocalAuthApplicationServiceTest {

    @Test
    void shouldStorePasswordHashAndReturnTokenWithoutHash() {
        // 这个测试函数的作用是验证注册流程只保存密码哈希，并返回最小身份令牌响应。
        LocalUserRepository repository = mock(LocalUserRepository.class);
        PasswordHashService passwordHashService = mock(PasswordHashService.class);
        JwtTokenService jwtTokenService = mock(JwtTokenService.class);
        LocalAuthApplicationService service = new LocalAuthApplicationService(
                repository, passwordHashService, jwtTokenService);
        given(repository.findByUsername("freeze")).willReturn(Optional.empty());
        given(passwordHashService.hash("safe-password")).willReturn("pbkdf2-hash");
        given(repository.save(org.mockito.ArgumentMatchers.any())).willAnswer(invocation -> invocation.getArgument(0));
        given(jwtTokenService.issue(org.mockito.ArgumentMatchers.any())).willReturn("jwt-token");
        given(jwtTokenService.expiresSeconds()).willReturn(3600L);

        AuthTokenResponse response = service.register(new UserRegisterRequest(
                "Freeze", "safe-password", "Freeze"));

        ArgumentCaptor<LocalUser> captor = ArgumentCaptor.forClass(LocalUser.class);
        verify(repository).save(captor.capture());
        assertEquals("freeze", captor.getValue().username());
        assertEquals("pbkdf2-hash", captor.getValue().passwordHash());
        assertFalse(response.accessToken().contains("pbkdf2-hash"));
        assertEquals("jwt-token", response.accessToken());
    }

    @Test
    void shouldLoginWithVerifiedPassword() {
        // 这个测试函数的作用是验证登录成功后会签发新令牌。
        LocalUserRepository repository = mock(LocalUserRepository.class);
        PasswordHashService passwordHashService = mock(PasswordHashService.class);
        JwtTokenService jwtTokenService = mock(JwtTokenService.class);
        LocalAuthApplicationService service = new LocalAuthApplicationService(
                repository, passwordHashService, jwtTokenService);
        LocalUser user = new LocalUser("user-001", "freeze", "Freeze", "hash", true,
                java.time.Instant.now(), java.time.Instant.now());
        given(repository.findByUsername("freeze")).willReturn(Optional.of(user));
        given(passwordHashService.matches("safe-password", "hash")).willReturn(true);
        given(jwtTokenService.issue(user)).willReturn("jwt-token");

        AuthTokenResponse response = service.login(new UserLoginRequest("freeze", "safe-password"));

        assertEquals("user-001", response.userId());
        assertEquals("jwt-token", response.accessToken());
    }
}
