package com.memoecho.eventcenter.service;

import com.memoecho.eventcenter.config.DownstreamServiceProperties;
import com.memoecho.eventcenter.dto.TaskServiceTaskResponse;
import org.springframework.core.ParameterizedTypeReference;
import org.springframework.stereotype.Component;
import org.springframework.web.client.RestClient;
import org.springframework.web.client.RestClientException;

import java.util.Collections;
import java.util.List;

@Component
public class TaskServiceQueryClient {

    private final RestClient restClient;
    private final DownstreamServiceProperties properties;

    public TaskServiceQueryClient(RestClient restClient, DownstreamServiceProperties properties) {
        // 这个构造函数的作用是注入 HTTP 客户端和下游服务地址配置，统一管理任务查询依赖。
        this.restClient = restClient;
        this.properties = properties;
    }

    public List<TaskServiceTaskResponse> listPendingTasks(String senderId, Integer limit) {
        // 这个函数的作用是向 task-service 拉取当前用户的待办任务，供登录后摘要聚合使用。
        try {
            List<TaskServiceTaskResponse> tasks = restClient.get()
                    .uri(
                            properties.getTaskServiceBaseUrl() + "/internal/tasks?senderId={senderId}&onlyPending=true&limit={limit}",
                            senderId,
                            limit == null ? 5 : limit
                    )
                    .retrieve()
                    .body(new ParameterizedTypeReference<>() {
                    });
            return tasks != null ? tasks : List.of();
        } catch (RestClientException ex) {
            return Collections.emptyList();
        }
    }
}
