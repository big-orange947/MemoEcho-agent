package com.memoecho.eventcenter.service;

import com.memoecho.eventcenter.config.DownstreamServiceProperties;
import com.memoecho.eventcenter.dto.ScheduleServiceScheduleResponse;
import org.springframework.core.ParameterizedTypeReference;
import org.springframework.stereotype.Component;
import org.springframework.web.client.RestClient;
import org.springframework.web.client.RestClientException;

import java.util.Collections;
import java.util.List;

@Component
public class ScheduleServiceQueryClient {

    private final RestClient restClient;
    private final DownstreamServiceProperties properties;

    public ScheduleServiceQueryClient(RestClient restClient, DownstreamServiceProperties properties) {
        // 这个构造函数的作用是注入 HTTP 客户端和日程服务配置，统一管理摘要聚合所需的日程查询。
        this.restClient = restClient;
        this.properties = properties;
    }

    public List<ScheduleServiceScheduleResponse> listSchedules(String senderId) {
        // 这个函数的作用是向 schedule-service 拉取当前用户的全部日程，再由上层聚合筛出今天和最近安排。
        try {
            List<ScheduleServiceScheduleResponse> schedules = restClient.get()
                    .uri(
                            properties.getScheduleServiceBaseUrl() + "/internal/schedules?senderId={senderId}",
                            senderId
                    )
                    .retrieve()
                    .body(new ParameterizedTypeReference<>() {
                    });
            return schedules != null ? schedules : List.of();
        } catch (RestClientException ex) {
            return Collections.emptyList();
        }
    }
}
