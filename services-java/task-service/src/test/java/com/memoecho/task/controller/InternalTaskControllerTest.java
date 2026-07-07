package com.memoecho.task.controller;

import com.fasterxml.jackson.databind.ObjectMapper;
import com.memoecho.task.dto.CreateTaskRequest;
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
class InternalTaskControllerTest {

    @Autowired
    private MockMvc mockMvc;

    @Autowired
    private ObjectMapper objectMapper;

    @Test
    void shouldCreateAndQueryTask() throws Exception {
        CreateTaskRequest request = new CreateTaskRequest(
                "qq:message:group:task-001",
                "qq",
                "1098307542",
                "2597164807",
                "finish project report",
                "finish the project report and send it before tomorrow noon",
                LocalDateTime.of(2026, 7, 8, 12, 0, 0),
                "high",
                "pending",
                "high"
        );

        mockMvc.perform(post("/internal/tasks")
                        .contentType(MediaType.APPLICATION_JSON)
                        .content(objectMapper.writeValueAsString(request)))
                .andExpect(status().isOk())
                .andExpect(jsonPath("$.sourceEventId").value("qq:message:group:task-001"))
                .andExpect(jsonPath("$.title").value("finish project report"))
                .andExpect(jsonPath("$.priority").value("high"));

        mockMvc.perform(get("/internal/tasks")
                        .param("chatId", "1098307542"))
                .andExpect(status().isOk())
                .andExpect(jsonPath("$[0].chatId").value("1098307542"))
                .andExpect(jsonPath("$[0].senderId").value("2597164807"));
    }

    @Test
    void shouldBeIdempotentBySourceEventId() throws Exception {
        CreateTaskRequest request = new CreateTaskRequest(
                "duplicate-task-source",
                "qq",
                "1098307542",
                "2597164807",
                "submit slides",
                "submit the demo slides today",
                LocalDateTime.of(2026, 7, 7, 18, 0, 0),
                "high",
                "pending",
                "medium"
        );

        mockMvc.perform(post("/internal/tasks")
                        .contentType(MediaType.APPLICATION_JSON)
                        .content(objectMapper.writeValueAsString(request)))
                .andExpect(status().isOk())
                .andExpect(jsonPath("$.sourceEventId").value("duplicate-task-source"));

        mockMvc.perform(post("/internal/tasks")
                        .contentType(MediaType.APPLICATION_JSON)
                        .content(objectMapper.writeValueAsString(request)))
                .andExpect(status().isOk())
                .andExpect(jsonPath("$.sourceEventId").value("duplicate-task-source"));

        mockMvc.perform(get("/internal/tasks")
                        .param("sourceEventId", "duplicate-task-source"))
                .andExpect(status().isOk())
                .andExpect(jsonPath("$.length()").value(1));
    }
}
