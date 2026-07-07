package com.memoecho.eventcenter.controller;

import com.memoecho.eventcenter.dto.DispatchResult;
import com.memoecho.eventcenter.dto.EventIngestResponse;
import com.memoecho.eventcenter.dto.UnifiedEventPayload;
import com.memoecho.eventcenter.service.EventCenterApplicationService;
import org.junit.jupiter.api.Test;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.boot.test.autoconfigure.web.servlet.WebMvcTest;
import org.springframework.boot.test.mock.mockito.MockBean;
import org.springframework.http.MediaType;
import org.springframework.test.web.servlet.MockMvc;

import static org.mockito.ArgumentMatchers.any;
import static org.mockito.BDDMockito.given;
import static org.springframework.test.web.servlet.request.MockMvcRequestBuilders.post;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.jsonPath;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.status;

@WebMvcTest(InternalEventController.class)
class InternalEventControllerTest {

    @Autowired
    private MockMvc mockMvc;

    @MockBean
    private EventCenterApplicationService applicationService;

    @Test
    void shouldAcceptUnifiedEvent() throws Exception {
        given(applicationService.ingest(any(UnifiedEventPayload.class)))
                .willReturn(new EventIngestResponse(
                        "qq:message:group:1",
                        true,
                        false,
                        new DispatchResult(true, 200, null, null),
                        "Event accepted by event center."
                ));

        String payload = """
                {
                  "eventId": "qq:message:group:1",
                  "platform": "qq",
                  "eventType": "message",
                  "chatType": "group",
                  "chatId": "138178088",
                  "text": "meeting at 14:00 in A01-N105"
                }
                """;

        mockMvc.perform(post("/internal/events/ingest")
                        .contentType(MediaType.APPLICATION_JSON)
                        .content(payload))
                .andExpect(status().isOk())
                .andExpect(jsonPath("$.accepted").value(true))
                .andExpect(jsonPath("$.duplicate").value(false))
                .andExpect(jsonPath("$.eventId").value("qq:message:group:1"));
    }
}
