package com.memoecho.eventcenter.controller;

import com.memoecho.eventcenter.dto.SecureAssetResponse;
import com.memoecho.eventcenter.dto.SecureAssetRuntimeResponse;
import com.memoecho.eventcenter.dto.SecureAssetUpsertRequest;
import com.memoecho.eventcenter.service.LocalUserContextResolver;
import com.memoecho.eventcenter.service.SecureAssetApplicationService;
import org.junit.jupiter.api.Test;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.boot.test.autoconfigure.web.servlet.WebMvcTest;
import org.springframework.boot.test.mock.mockito.MockBean;
import org.springframework.http.MediaType;
import org.springframework.test.web.servlet.MockMvc;

import java.time.Instant;
import java.util.List;

import static org.mockito.ArgumentMatchers.any;
import static org.mockito.ArgumentMatchers.eq;
import static org.mockito.BDDMockito.given;
import static org.mockito.Mockito.verify;
import static org.springframework.test.web.servlet.request.MockMvcRequestBuilders.get;
import static org.springframework.test.web.servlet.request.MockMvcRequestBuilders.post;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.jsonPath;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.status;

/** 验证桌面端元数据接口与 Runtime 明文接口之间的认证边界。 */
@WebMvcTest(InternalSecureAssetController.class)
class InternalSecureAssetControllerTest {

    @Autowired
    private MockMvc mockMvc;

    @MockBean
    private SecureAssetApplicationService applicationService;

    @MockBean
    private LocalUserContextResolver userContextResolver;

    /** 普通资产列表响应不包含 content 字段。 */
    @Test
    void shouldReturnMetadataWithoutPlainContent() throws Exception {
        given(userContextResolver.resolve(any(), any())).willReturn("freeze");
        given(applicationService.listAssets("freeze")).willReturn(List.of(metadata()));

        mockMvc.perform(get("/internal/secure-assets"))
                .andExpect(status().isOk())
                .andExpect(jsonPath("$[0].name").value("微信收款码"))
                .andExpect(jsonPath("$[0].contentConfigured").value(true))
                .andExpect(jsonPath("$[0].content").doesNotExist());
    }

    /** Runtime 必须通过独立服务令牌解析用户后才能获取正文。 */
    @Test
    void shouldResolvePlainContentOnlyForRuntimeToken() throws Exception {
        given(userContextResolver.resolveRuntimeUser("runtime-token", "freeze")).willReturn("freeze");
        given(applicationService.resolveForRuntime("freeze", "asset-1")).willReturn(new SecureAssetRuntimeResponse(
                "asset-1", "卡密", "LICENSE_CODE", "", "text/plain", "SECRET-001",
                "SINGLE_USE", 0, Instant.parse("2026-07-17T01:00:00Z")
        ));

        mockMvc.perform(post("/internal/secure-assets/asset-1/resolve")
                        .header("X-Memo-Echo-Runtime-Token", "runtime-token")
                        .header("X-Memo-Echo-User-Id", "freeze"))
                .andExpect(status().isOk())
                .andExpect(jsonPath("$.content").value("SECRET-001"));

        verify(userContextResolver).resolveRuntimeUser("runtime-token", "freeze");
    }

    /** 创建接口把请求归属固定到当前认证用户。 */
    @Test
    void shouldCreateAssetForAuthenticatedUser() throws Exception {
        given(userContextResolver.resolve(any(), any())).willReturn("freeze");
        given(applicationService.createAsset(eq("freeze"), any(SecureAssetUpsertRequest.class)))
                .willReturn(metadata());

        mockMvc.perform(post("/internal/secure-assets")
                        .contentType(MediaType.APPLICATION_JSON)
                        .content("""
                                {
                                  "name":"微信收款码",
                                  "type":"PAYMENT_CODE",
                                  "contentType":"image/png",
                                  "content":"data:image/png;base64,demo",
                                  "usagePolicy":"REUSABLE"
                                }
                                """))
                .andExpect(status().isOk())
                .andExpect(jsonPath("$.id").value("asset-1"));
    }

    /** 构造不包含正文的元数据响应。 */
    private SecureAssetResponse metadata() {
        Instant now = Instant.parse("2026-07-17T00:00:00Z");
        return new SecureAssetResponse(
                "asset-1", "微信收款码", "PAYMENT_CODE", "成交后发送", "image/png",
                "REUSABLE", null, true, true, now, now, null
        );
    }
}
