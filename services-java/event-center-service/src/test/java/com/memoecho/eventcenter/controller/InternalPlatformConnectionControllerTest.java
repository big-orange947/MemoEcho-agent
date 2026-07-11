package com.memoecho.eventcenter.controller;

import com.memoecho.eventcenter.dto.PlatformConnectionResponse;
import com.memoecho.eventcenter.service.PlatformConnectionApplicationService;
import com.memoecho.eventcenter.service.LocalUserContextResolver;
import org.junit.jupiter.api.Test;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.boot.test.autoconfigure.web.servlet.WebMvcTest;
import org.springframework.boot.test.mock.mockito.MockBean;
import org.springframework.test.web.servlet.MockMvc;

import java.util.List;

import static org.mockito.BDDMockito.given;
import static org.mockito.Mockito.verify;
import static org.springframework.test.web.servlet.request.MockMvcRequestBuilders.get;
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
}
