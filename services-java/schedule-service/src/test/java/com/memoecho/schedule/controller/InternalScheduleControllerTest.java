package com.memoecho.schedule.controller;

import com.fasterxml.jackson.databind.ObjectMapper;
import com.memoecho.schedule.dto.CreateScheduleRequest;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.BeforeEach;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.boot.test.autoconfigure.web.servlet.AutoConfigureMockMvc;
import org.springframework.boot.test.context.SpringBootTest;
import org.springframework.http.MediaType;
import org.springframework.jdbc.core.JdbcTemplate;
import org.springframework.test.web.servlet.MockMvc;

import java.time.LocalDateTime;

import static org.springframework.test.web.servlet.request.MockMvcRequestBuilders.get;
import static org.springframework.test.web.servlet.request.MockMvcRequestBuilders.delete;
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

    @Autowired
    private JdbcTemplate jdbcTemplate;

    @BeforeEach
    void clearSchedules() {
        // 这个函数的作用是在每个接口测试前清空 H2 测试表，避免测试顺序影响列表断言。
        jdbcTemplate.update("DELETE FROM schedule_item");
    }

    @Test
    void shouldCreateAndQuerySchedule() throws Exception {
        String sourceEventId = "qq:message:group:" + System.nanoTime();
        CreateScheduleRequest request = new CreateScheduleRequest(
                sourceEventId,
                "qq",
                "138178088",
                "2597164807",
                "考研经验分享会",
                LocalDateTime.now().plusDays(2).withHour(14).withMinute(0).withSecond(0).withNano(0),
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
                .andExpect(jsonPath("$.sourceEventId").value(sourceEventId))
                .andExpect(jsonPath("$.title").value("考研经验分享会"))
                .andExpect(jsonPath("$.location").value("A01-N105"));

        mockMvc.perform(get("/internal/schedules")
                        .param("sourceEventId", sourceEventId))
                .andExpect(status().isOk())
                .andExpect(jsonPath("$[0].chatId").value("138178088"))
                .andExpect(jsonPath("$[0].senderId").value("2597164807"));
    }

    @Test
    void shouldBeIdempotentBySourceEventId() throws Exception {
        String sourceEventId = "duplicate-source-" + System.nanoTime();
        CreateScheduleRequest request = new CreateScheduleRequest(
                sourceEventId,
                "qq",
                "138178088",
                "2597164807",
                "节能减排大赛路演",
                LocalDateTime.now().plusDays(3).withHour(12).withMinute(0).withSecond(0).withNano(0),
                LocalDateTime.now().plusDays(3).withHour(14).withMinute(0).withSecond(0).withNano(0),
                "B12彩虹长廊侧广场",
                "中午12:00-14:00将有参赛队伍现场演示",
                "各位同学",
                "high"
        );

        mockMvc.perform(post("/internal/schedules")
                        .contentType(MediaType.APPLICATION_JSON)
                        .content(objectMapper.writeValueAsString(request)))
                .andExpect(status().isOk())
                .andExpect(jsonPath("$.sourceEventId").value(sourceEventId));

        mockMvc.perform(post("/internal/schedules")
                        .contentType(MediaType.APPLICATION_JSON)
                        .content(objectMapper.writeValueAsString(request)))
                .andExpect(status().isOk())
                .andExpect(jsonPath("$.sourceEventId").value(sourceEventId));

        mockMvc.perform(get("/internal/schedules")
                        .param("sourceEventId", sourceEventId))
                .andExpect(status().isOk())
                .andExpect(jsonPath("$.length()").value(1));
    }

    @Test
    void shouldGetAndDeleteSchedule() throws Exception {
        // 这个测试验证 event-center 依赖的单条查询和删除接口形成完整闭环。
        String sourceEventId = "manual-test-" + System.nanoTime();
        CreateScheduleRequest request = new CreateScheduleRequest(
                sourceEventId,
                "local",
                "manual",
                "owner-1",
                "手动添加的日程",
                LocalDateTime.now().plusDays(4).withNano(0),
                null,
                "线上",
                "手动添加的日程",
                null,
                "manual"
        );

        String response = mockMvc.perform(post("/internal/schedules")
                        .contentType(MediaType.APPLICATION_JSON)
                        .content(objectMapper.writeValueAsString(request)))
                .andExpect(status().isOk())
                .andReturn()
                .getResponse()
                .getContentAsString();
        String id = objectMapper.readTree(response).path("id").asText();

        mockMvc.perform(get("/internal/schedules/{id}", id))
                .andExpect(status().isOk())
                .andExpect(jsonPath("$.title").value("手动添加的日程"));

        mockMvc.perform(delete("/internal/schedules/{id}", id))
                .andExpect(status().isNoContent());
        mockMvc.perform(get("/internal/schedules/{id}", id))
                .andExpect(status().isNotFound());
    }

    @Test
    void shouldRemoveExpiredScheduleBeforeListing() throws Exception {
        // 这个测试验证即使定时任务尚未运行，列表接口也不会把已经过时的日程返回给客户端。
        String sourceEventId = "expired-source-" + System.nanoTime();
        CreateScheduleRequest request = new CreateScheduleRequest(
                sourceEventId,
                "qq",
                "138178088",
                "2597164807",
                "已结束的项目例会",
                LocalDateTime.now().minusMinutes(30).withNano(0),
                null,
                "A01-N105",
                "这条日程的开始时间已经过去",
                null,
                "high"
        );

        mockMvc.perform(post("/internal/schedules")
                        .contentType(MediaType.APPLICATION_JSON)
                        .content(objectMapper.writeValueAsString(request)))
                .andExpect(status().isOk());

        mockMvc.perform(get("/internal/schedules")
                        .param("sourceEventId", sourceEventId))
                .andExpect(status().isOk())
                .andExpect(jsonPath("$.length()").value(0));
    }
}
