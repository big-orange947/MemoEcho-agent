package com.memoecho.eventcenter.controller;

import com.memoecho.eventcenter.dto.AuthTokenResponse;
import com.memoecho.eventcenter.dto.UserLoginRequest;
import com.memoecho.eventcenter.service.LocalAuthApplicationService;
import org.junit.jupiter.api.Test;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.boot.test.autoconfigure.web.servlet.WebMvcTest;
import org.springframework.boot.test.mock.mockito.MockBean;
import org.springframework.http.MediaType;
import org.springframework.test.web.servlet.MockMvc;

import static org.mockito.BDDMockito.given;
import static org.springframework.test.web.servlet.request.MockMvcRequestBuilders.post;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.jsonPath;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.status;

@WebMvcTest(LocalAuthController.class)
class LocalAuthControllerTest {

    @Autowired
    private MockMvc mockMvc;

    @MockBean
    private LocalAuthApplicationService applicationService;

    @Test
    void shouldReturnBearerTokenAfterLogin() throws Exception {
        // 这个测试函数的作用是验证登录接口返回前端可直接使用的 Bearer Token 结构。
        given(applicationService.login(new UserLoginRequest("freeze", "safe-password")))
                .willReturn(new AuthTokenResponse(
                        "Bearer", "jwt-token", 3600, "user-001", "freeze", "Freeze"));

        mockMvc.perform(post("/api/auth/login")
                        .contentType(MediaType.APPLICATION_JSON)
                        .content("""
                                {"username":"freeze","password":"safe-password"}
                                """))
                .andExpect(status().isOk())
                .andExpect(jsonPath("$.tokenType").value("Bearer"))
                .andExpect(jsonPath("$.accessToken").value("jwt-token"))
                .andExpect(jsonPath("$.userId").value("user-001"));
    }
}
