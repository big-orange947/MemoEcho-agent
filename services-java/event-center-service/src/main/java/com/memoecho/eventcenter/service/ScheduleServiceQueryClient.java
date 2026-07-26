package com.memoecho.eventcenter.service;

import com.memoecho.eventcenter.config.DownstreamServiceProperties;
import com.memoecho.eventcenter.dto.ScheduleServiceCreateRequest;
import com.memoecho.eventcenter.dto.ScheduleServiceScheduleResponse;
import org.springframework.core.ParameterizedTypeReference;
import org.springframework.http.HttpStatus;
import org.springframework.stereotype.Component;
import org.springframework.web.client.HttpClientErrorException;
import org.springframework.web.client.RestClient;
import org.springframework.web.client.RestClientException;
import org.springframework.web.server.ResponseStatusException;

import java.util.Collections;
import java.util.List;
import java.util.Optional;

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

    /**
     * 读取当前本地工作区的全部日程。
     *
     * <p>日程记录中的 senderId 表示“原始通知由谁发送”，并不表示日程归谁所有。
     * 因此工作台不能拿 NapCat 登录账号过滤 senderId，否则好友或群成员发布的日程会被隐藏。</p>
     */
    public List<ScheduleServiceScheduleResponse> listWorkspaceSchedules() {
        try {
            List<ScheduleServiceScheduleResponse> schedules = restClient.get()
                    .uri(properties.getScheduleServiceBaseUrl() + "/internal/schedules")
                    .retrieve()
                    .body(new ParameterizedTypeReference<>() {
                    });
            return schedules != null ? schedules : List.of();
        } catch (RestClientException ex) {
            return Collections.emptyList();
        }
    }

    public ScheduleServiceScheduleResponse createSchedule(ScheduleServiceCreateRequest request) {
        // 这个函数的作用是把经过 event-center 鉴权和补全来源信息的手动日程写入 schedule-service。
        try {
            ScheduleServiceScheduleResponse response = restClient.post()
                    .uri(properties.getScheduleServiceBaseUrl() + "/internal/schedules")
                    .body(request)
                    .retrieve()
                    .body(ScheduleServiceScheduleResponse.class);
            if (response == null) {
                throw new ResponseStatusException(HttpStatus.BAD_GATEWAY, "日程服务返回了空结果。");
            }
            return response;
        } catch (RestClientException exception) {
            throw new ResponseStatusException(HttpStatus.BAD_GATEWAY, "无法写入日程服务。", exception);
        }
    }

    public Optional<ScheduleServiceScheduleResponse> getSchedule(String scheduleId) {
        // 这个函数的作用是读取单条日程详情；下游 404 会保留为 Optional.empty()。
        try {
            return Optional.ofNullable(restClient.get()
                    .uri(properties.getScheduleServiceBaseUrl() + "/internal/schedules/{id}", scheduleId)
                    .retrieve()
                    .body(ScheduleServiceScheduleResponse.class));
        } catch (HttpClientErrorException.NotFound exception) {
            return Optional.empty();
        } catch (RestClientException exception) {
            throw new ResponseStatusException(HttpStatus.BAD_GATEWAY, "无法读取日程服务。", exception);
        }
    }

    public boolean deleteSchedule(String scheduleId) {
        // 这个函数的作用是在 event-center 完成归属校验后调用下游删除，并区分不存在与服务异常。
        try {
            restClient.delete()
                    .uri(properties.getScheduleServiceBaseUrl() + "/internal/schedules/{id}", scheduleId)
                    .retrieve()
                    .toBodilessEntity();
            return true;
        } catch (HttpClientErrorException.NotFound exception) {
            return false;
        } catch (RestClientException exception) {
            throw new ResponseStatusException(HttpStatus.BAD_GATEWAY, "无法删除日程。", exception);
        }
    }
}
