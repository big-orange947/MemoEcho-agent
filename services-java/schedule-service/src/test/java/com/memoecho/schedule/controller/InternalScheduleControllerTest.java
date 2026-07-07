package com.memoecho.schedule.controller;

import com.fasterxml.jackson.databind.ObjectMapper;
import com.memoecho.schedule.dto.CreateScheduleRequest;
import org.junit.jupiter.api.Test;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.boot.test.autoconfigure.web.servlet.AutoConfigureMockMvc;
import org.springframework.boot.test.context.SpringBootTest;
import org.springframework.http.MediaType;
import org.springframework.test.web.servlet.MockMvc;

import java.time.LocalDateTime;

import static org.springframework.test.web.servlet.request.MockMvcRequestBuilders.get;
import static org.springframework.test.web.servlet.request.MockMvcRequestBuilders.post;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.jsonPath;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.status;

@SpringBootTest
@AutoConfigureMockMvc
class InternalScheduleControllerTest {

    @Autowired
    private MockMvc mockMvc;

    @Autowired
    private ObjectMapper objectMapper;

    @Test
    void shouldCreateAndQuerySchedule() throws Exception {
        CreateScheduleRequest request = new CreateScheduleRequest(
                "qq:message:group:1843661133",
                "qq",
                "138178088",
                "2597164807",
                "考研经验分享会",
                LocalDateTime.of(2026, 7, 6, 14, 0, 0),
                null,
                "A01-N105",
                "今天下午14:00在A01-N105举办分享会",
                "感兴趣的同学",
                "high"
        );

        mockMvc.perform(post("/internal/schedules")
                        .contentType(MediaType.APPLICATION_JSON)
                        .content(objectMapper.writeValueAsString(request)))
                .andExpect(status().isOk())
                .andExpect(jsonPath("$.sourceEventId").value("qq:message:group:1843661133"))
                .andExpect(jsonPath("$.title").value("考研经验分享会"))
                .andExpect(jsonPath("$.location").value("A01-N105"));

        mockMvc.perform(get("/internal/schedules")
                        .param("chatId", "138178088"))
                .andExpect(status().isOk())
                .andExpect(jsonPath("$[0].chatId").value("138178088"))
                .andExpect(jsonPath("$[0].senderId").value("2597164807"));
    }

    @Test
    void shouldBeIdempotentBySourceEventId() throws Exception {
        CreateScheduleRequest request = new CreateScheduleRequest(
                "duplicate-source-id",
                "qq",
                "138178088",
                "2597164807",
                "节能减排大赛路演",
                LocalDateTime.of(2026, 7, 6, 12, 0, 0),
                LocalDateTime.of(2026, 7, 6, 14, 0, 0),
                "B12彩虹长廊侧广场",
                "中午12:00-14:00将有参赛队伍现场演示",
                "各位同学",
                "high"
        );

        mockMvc.perform(post("/internal/schedules")
                        .contentType(MediaType.APPLICATION_JSON)
                        .content(objectMapper.writeValueAsString(request)))
                .andExpect(status().isOk())
                .andExpect(jsonPath("$.sourceEventId").value("duplicate-source-id"));

        mockMvc.perform(post("/internal/schedules")
                        .contentType(MediaType.APPLICATION_JSON)
                        .content(objectMapper.writeValueAsString(request)))
                .andExpect(status().isOk())
                .andExpect(jsonPath("$.sourceEventId").value("duplicate-source-id"));

        mockMvc.perform(get("/internal/schedules")
                        .param("sourceEventId", "duplicate-source-id"))
                .andExpect(status().isOk())
                .andExpect(jsonPath("$.length()").value(1));
    }
}

