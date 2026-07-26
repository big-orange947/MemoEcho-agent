package com.memoecho.eventcenter.controller;

import com.fasterxml.jackson.databind.node.JsonNodeFactory;
import com.memoecho.eventcenter.dto.PlatformConnectionResponse;
import com.memoecho.eventcenter.service.PlatformConnectionApplicationService;
import com.memoecho.eventcenter.service.LocalUserContextResolver;
import com.memoecho.eventcenter.service.QqConnectorMessageClient;
import org.junit.jupiter.api.Test;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.boot.test.autoconfigure.web.servlet.WebMvcTest;
import org.springframework.boot.test.mock.mockito.MockBean;
import org.springframework.test.web.servlet.MockMvc;

import java.util.List;

import static org.mockito.BDDMockito.given;
import static org.mockito.Mockito.verify;
import static org.springframework.test.web.servlet.request.MockMvcRequestBuilders.get;
import static org.springframework.test.web.servlet.request.MockMvcRequestBuilders.post;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.jsonPath;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.status;

@WebMvcTest(InternalPlatformConnectionController.class)
class InternalPlatformConnectionControllerTest {

    @Autowired
    private MockMvc mockMvc;

    @MockBean
    private PlatformConnectionApplicationService applicationService;

    @MockBean
    private LocalUserContextResolver userContextResolver;

    @MockBean
    private QqConnectorMessageClient qqConnectorMessageClient;

    @Test
    void shouldListOnlyCurrentUserConnections() throws Exception {
        // 这个测试函数的作用是验证开发期用户头会传给应用服务，并且响应只包含脱敏连接状态。
        given(userContextResolver.resolve(null, "user-001")).willReturn("user-001");
        given(applicationService.listConnections("user-001")).willReturn(List.of(
                new PlatformConnectionResponse(
                        "connection-1", "user-001", "我的 QQ", "qq", "napcat", true,
                        true, "3969785168", "哈吉仙", "HEALTHY", "NapCat 已连接。",
                        "2026-07-10T10:00:00Z", true)
        ));

        mockMvc.perform(get("/internal/connections").header("X-Memo-Echo-User-Id", "user-001"))
                .andExpect(status().isOk())
                .andExpect(jsonPath("$[0].id").value("connection-1"))
                .andExpect(jsonPath("$[0].userId").value("user-001"))
                .andExpect(jsonPath("$[0].hasCredential").value(true))
                .andExpect(jsonPath("$[0].credential").doesNotExist());

        verify(applicationService).listConnections("user-001");
    }

    @Test
    void shouldStartQqQrLoginWithoutExposingWebUiCredential() throws Exception {
        // 这个测试函数的作用是验证桌面端只能获取二维码状态，NapCat WebUI 凭据不会穿过 Event Center。
        given(userContextResolver.resolve(null, "user-001")).willReturn("user-001");
        given(qqConnectorMessageClient.startQrLogin()).willReturn(JsonNodeFactory.instance.objectNode()
                .put("state", "WAITING_SCAN")
                .put("qrCodeUrl", "data:image/png;base64,example")
                .put("message", "请使用手机 QQ 扫码"));

        mockMvc.perform(post("/internal/connections/qq/qr-login")
                        .header("X-Memo-Echo-User-Id", "user-001"))
                .andExpect(status().isOk())
                .andExpect(jsonPath("$.state").value("WAITING_SCAN"))
                .andExpect(jsonPath("$.qrCodeUrl").value("data:image/png;base64,example"))
                .andExpect(jsonPath("$.credential").doesNotExist());

        verify(qqConnectorMessageClient).startQrLogin();
    }

    @Test
    void shouldRefreshConnectionProfileAfterQrLoginCompletes() throws Exception {
        // 这个测试函数的作用是验证扫码完成后会刷新当前用户的 QQ 连接状态，客户端无需再手工检测。
        given(userContextResolver.resolve(null, "user-001")).willReturn("user-001");
        given(qqConnectorMessageClient.fetchQrLoginStatus()).willReturn(JsonNodeFactory.instance.objectNode()
                .put("state", "CONNECTED")
                .put("accountId", "3969785168")
                .put("accountName", "哈吉仙")
                .put("onebotConfigured", true));

        mockMvc.perform(get("/internal/connections/qq/qr-login/status")
                        .header("X-Memo-Echo-User-Id", "user-001"))
                .andExpect(status().isOk())
                .andExpect(jsonPath("$.state").value("CONNECTED"))
                .andExpect(jsonPath("$.onebotConfigured").value(true));

        verify(applicationService).refreshLocalQqConnection("user-001");
    }
}
