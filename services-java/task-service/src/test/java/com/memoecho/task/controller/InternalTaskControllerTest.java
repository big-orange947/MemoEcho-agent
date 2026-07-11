package com.memoecho.task.controller;

import com.fasterxml.jackson.databind.ObjectMapper;
import com.memoecho.task.dto.CreateTaskRequest;
import org.junit.jupiter.api.Test;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.boot.test.autoconfigure.web.servlet.AutoConfigureMockMvc;
import org.springframework.boot.test.context.SpringBootTest;
import org.springframework.http.MediaType;
import org.springframework.test.web.servlet.MockMvc;

import java.time.LocalDate;
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
        // 这个测试函数的作用是验证基础创建和按 chatId 查询任务的主链路可用。
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
        // 这个测试函数的作用是验证同一来源事件重复写入时不会产生重复任务。
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

    @Test
    void shouldSupportTodayPendingPriorityFiltersAndSortByUrgency() throws Exception {
        // 这个测试函数的作用是验证 todayOnly、onlyPending、priority 和排序规则能一起工作。
        LocalDate today = LocalDate.now();

        CreateTaskRequest overdue = new CreateTaskRequest(
                "source-overdue",
                "qq",
                "1098307542",
                "2597164807",
                "fix overdue issue",
                "overdue task",
                today.minusDays(1).atTime(18, 0),
                "high",
                "pending",
                "high"
        );
        CreateTaskRequest todayHigh = new CreateTaskRequest(
                "source-today-high",
                "qq",
                "1098307542",
                "2597164807",
                "prepare demo",
                "today task",
                today.atTime(14, 0),
                "high",
                "pending",
                "high"
        );
        CreateTaskRequest todayDone = new CreateTaskRequest(
                "source-today-done",
                "qq",
                "1098307542",
                "2597164807",
                "archived task",
                "done task",
                today.atTime(16, 0),
                "high",
                "done",
                "medium"
        );
        CreateTaskRequest tomorrowHigh = new CreateTaskRequest(
                "source-tomorrow-high",
                "qq",
                "1098307542",
                "2597164807",
                "write summary",
                "tomorrow task",
                today.plusDays(1).atTime(10, 0),
                "high",
                "pending",
                "medium"
        );

        mockMvc.perform(post("/internal/tasks").contentType(MediaType.APPLICATION_JSON).content(objectMapper.writeValueAsString(overdue)))
                .andExpect(status().isOk());
        mockMvc.perform(post("/internal/tasks").contentType(MediaType.APPLICATION_JSON).content(objectMapper.writeValueAsString(todayHigh)))
                .andExpect(status().isOk());
        mockMvc.perform(post("/internal/tasks").contentType(MediaType.APPLICATION_JSON).content(objectMapper.writeValueAsString(todayDone)))
                .andExpect(status().isOk());
        mockMvc.perform(post("/internal/tasks").contentType(MediaType.APPLICATION_JSON).content(objectMapper.writeValueAsString(tomorrowHigh)))
                .andExpect(status().isOk());

        mockMvc.perform(get("/internal/tasks")
                        .param("chatId", "1098307542")
                        .param("priority", "high")
                        .param("todayOnly", "true")
                        .param("onlyPending", "true")
                        .param("limit", "5"))
                .andExpect(status().isOk())
                .andExpect(jsonPath("$.length()").value(1))
                .andExpect(jsonPath("$[0].sourceEventId").value("source-today-high"));

        mockMvc.perform(get("/internal/tasks")
                        .param("chatId", "1098307542")
                        .param("onlyPending", "true")
                        .param("limit", "3"))
                .andExpect(status().isOk())
                .andExpect(jsonPath("$[0].sourceEventId").value("source-overdue"))
                .andExpect(jsonPath("$[1].sourceEventId").value("source-today-high"));
    }
}
