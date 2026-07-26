package com.memoecho.eventcenter.controller;

import com.memoecho.eventcenter.dto.ConversationProgressResponse;
import com.memoecho.eventcenter.service.ConversationProgressApplicationService;
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

@WebMvcTest(InternalConversationProgressController.class)
class InternalConversationProgressControllerTest {

    @Autowired
    private MockMvc mockMvc;

    @MockBean
    private ConversationProgressApplicationService conversationProgressApplicationService;

    @MockBean
    private LocalUserContextResolver userContextResolver;

    @Test
    void shouldResolveCurrentUserAndReturnOnDemandSnapshot() throws Exception {
        // 这个测试函数的作用是验证桌面端点击查看时会携带当前用户范围查询，而不是访问无归属的全局消息。
        given(userContextResolver.resolve(null, "local-user")).willReturn("user-1");
        given(conversationProgressApplicationService.buildSnapshot(
                "user-1", "qq", "private", "10001", 60, null
        )).willReturn(new ConversationProgressResponse(
                "双方正在确认会员价格，目前轮到对方继续",
                true,
                "2026-07-13T10:02:00Z",
                true,
                "agent-1",
                List.of()
        ));

        mockMvc.perform(get("/internal/workspace/conversations/10001/progress")
                        .param("platform", "qq")
                        .param("chatType", "private")
                        .param("limit", "60"))
                .andExpect(status().isOk())
                .andExpect(jsonPath("$.summary").value("双方正在确认会员价格，目前轮到对方继续"))
                .andExpect(jsonPath("$.generatedByModel").value(true))
                .andExpect(jsonPath("$.summaryUpdated").value(true))
                .andExpect(jsonPath("$.latestAgentEventId").value("agent-1"))
                .andExpect(jsonPath("$.messages").isArray());

        verify(userContextResolver).resolve(null, "local-user");
        verify(conversationProgressApplicationService).buildSnapshot(
                "user-1", "qq", "private", "10001", 60, null
        );
    }
}
