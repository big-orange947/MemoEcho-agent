package com.memoecho.eventcenter.service;

import com.memoecho.eventcenter.dto.AuthTokenResponse;
import com.memoecho.eventcenter.dto.UserLoginRequest;
import com.memoecho.eventcenter.dto.UserRegisterRequest;
import com.memoecho.eventcenter.model.LocalUser;
import com.memoecho.eventcenter.repository.LocalUserRepository;
import org.springframework.dao.DuplicateKeyException;
import org.springframework.http.HttpStatus;
import org.springframework.stereotype.Service;
import org.springframework.web.server.ResponseStatusException;

import java.time.Instant;
import java.util.Locale;
import java.util.UUID;

@Service
public class LocalAuthApplicationService {

    private final LocalUserRepository repository;
    private final PasswordHashService passwordHashService;
    private final JwtTokenService jwtTokenService;

    public LocalAuthApplicationService(
            LocalUserRepository repository,
            PasswordHashService passwordHashService,
            JwtTokenService jwtTokenService
    ) {
        // 这个构造函数的作用是注入用户仓储、密码哈希器和 JWT 服务。
        this.repository = repository;
        this.passwordHashService = passwordHashService;
        this.jwtTokenService = jwtTokenService;
    }

    public AuthTokenResponse register(UserRegisterRequest request) {
        // 这个函数的作用是创建唯一用户名账户，密码只以 PBKDF2 哈希形式落库，并立即签发登录令牌。
        String username = normalizeUsername(request.username());
        if (repository.findByUsername(username).isPresent()) {
            throw new ResponseStatusException(HttpStatus.CONFLICT, "用户名已存在。");
        }
        Instant now = Instant.now();
        LocalUser user = new LocalUser(
                UUID.randomUUID().toString(), username, normalizeDisplayName(request.displayName(), username),
                passwordHashService.hash(request.password()), true, now, now);
        try {
            repository.save(user);
        } catch (DuplicateKeyException exception) {
            throw new ResponseStatusException(HttpStatus.CONFLICT, "用户名已存在。", exception);
        }
        return tokenResponse(user);
    }

    public AuthTokenResponse login(UserLoginRequest request) {
        // 这个函数的作用是校验用户名、账户状态和密码哈希，失败时统一返回 401，避免泄露账户存在性。
        LocalUser user = repository.findByUsername(normalizeUsername(request.username()))
                .orElseThrow(this::unauthorized);
        if (!user.enabled() || !passwordHashService.matches(request.password(), user.passwordHash())) {
            throw unauthorized();
        }
        return tokenResponse(user);
    }

    private AuthTokenResponse tokenResponse(LocalUser user) {
        // 这个函数的作用是生成统一登录响应，响应中不包含密码哈希。
        return new AuthTokenResponse(
                "Bearer", jwtTokenService.issue(user), jwtTokenService.expiresSeconds(),
                user.id(), user.username(), user.displayName());
    }

    private ResponseStatusException unauthorized() {
        // 这个函数的作用是统一登录失败响应，避免区分用户名不存在和密码错误。
        return new ResponseStatusException(HttpStatus.UNAUTHORIZED, "用户名或密码错误。");
    }

    private String normalizeUsername(String username) {
        // 这个函数的作用是把用户名统一为小写，使登录匹配与唯一索引保持一致。
        return username.trim().toLowerCase(Locale.ROOT);
    }

    private String normalizeDisplayName(String displayName, String username) {
        // 这个函数的作用是清理显示名称，未设置时回退到用户名。
        return displayName == null || displayName.isBlank() ? username : displayName.trim();
    }
}
