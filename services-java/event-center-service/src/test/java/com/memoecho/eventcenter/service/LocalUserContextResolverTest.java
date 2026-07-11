package com.memoecho.eventcenter.service;

import com.fasterxml.jackson.databind.ObjectMapper;
import com.memoecho.eventcenter.config.EventCenterSecurityProperties;
import com.memoecho.eventcenter.model.LocalUser;
import com.memoecho.eventcenter.repository.LocalUserRepository;
import org.junit.jupiter.api.Test;
import org.springframework.web.server.ResponseStatusException;

import java.time.Instant;
import java.util.Optional;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertThrows;
import static org.mockito.BDDMockito.given;
import static org.mockito.Mockito.mock;

class LocalUserContextResolverTest {

    @Test
    void shouldUseLocalDefaultAndNormalizeExplicitUserId() {
        // 这个测试函数的作用是验证缺省本地用户和显式用户标识都经过统一规范化。
        EventCenterSecurityProperties properties = new EventCenterSecurityProperties();
        LocalUserContextResolver resolver = new LocalUserContextResolver(
                new JwtTokenService(properties, new ObjectMapper()), properties, mock(LocalUserRepository.class));

        assertEquals("local-user", resolver.resolve("  "));
        assertEquals("user-001", resolver.resolve(" user-001 "));
    }

    /**
     * 验证 runtime 服务令牌只能代表存在且启用的本地用户执行内部调用。
     */
    @Test
    void shouldResolveRuntimeUserOnlyWithValidServiceToken() {
        EventCenterSecurityProperties properties = new EventCenterSecurityProperties();
        properties.setRuntimeToken("runtime-unit-token");
        LocalUserRepository repository = mock(LocalUserRepository.class);
        given(repository.findById("freeze")).willReturn(Optional.of(new LocalUser(
                "freeze", "freeze", "Freeze", "hash", true, Instant.now(), Instant.now()
        )));
        LocalUserContextResolver resolver = new LocalUserContextResolver(
                new JwtTokenService(properties, new ObjectMapper()), properties, repository);

        assertEquals("freeze", resolver.resolveRuntimeUser("runtime-unit-token", "freeze"));
        assertThrows(ResponseStatusException.class,
                () -> resolver.resolveRuntimeUser("wrong-token", "freeze"));
    }
}
