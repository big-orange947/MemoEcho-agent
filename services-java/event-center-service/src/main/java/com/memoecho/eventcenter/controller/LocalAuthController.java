package com.memoecho.eventcenter.controller;

import com.memoecho.eventcenter.dto.AuthTokenResponse;
import com.memoecho.eventcenter.dto.UserLoginRequest;
import com.memoecho.eventcenter.dto.UserRegisterRequest;
import com.memoecho.eventcenter.service.LocalAuthApplicationService;
import jakarta.validation.Valid;
import org.springframework.http.HttpStatus;
import org.springframework.http.ResponseEntity;
import org.springframework.web.bind.annotation.PostMapping;
import org.springframework.web.bind.annotation.RequestBody;
import org.springframework.web.bind.annotation.RequestMapping;
import org.springframework.web.bind.annotation.RestController;

@RestController
@RequestMapping("/api/auth")
public class LocalAuthController {

    private final LocalAuthApplicationService applicationService;

    public LocalAuthController(LocalAuthApplicationService applicationService) {
        // 这个构造函数的作用是注入本地认证应用服务。
        this.applicationService = applicationService;
    }

    @PostMapping("/register")
    public ResponseEntity<AuthTokenResponse> register(@Valid @RequestBody UserRegisterRequest request) {
        // 这个接口的作用是注册本地账户并返回首个 Bearer Token。
        return ResponseEntity.status(HttpStatus.CREATED).body(applicationService.register(request));
    }

    @PostMapping("/login")
    public ResponseEntity<AuthTokenResponse> login(@Valid @RequestBody UserLoginRequest request) {
        // 这个接口的作用是使用用户名和密码登录并签发 Bearer Token。
        return ResponseEntity.ok(applicationService.login(request));
    }
}
