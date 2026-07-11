package com.memoecho.eventcenter.config;

import org.springframework.boot.context.properties.ConfigurationProperties;

@ConfigurationProperties(prefix = "event-center.downstream")
public class DownstreamServiceProperties {

    private String taskServiceBaseUrl = "http://127.0.0.1:8094";
    private String scheduleServiceBaseUrl = "http://127.0.0.1:8092";
    private String qqConnectorBaseUrl = "http://127.0.0.1:8091";

    public String getTaskServiceBaseUrl() {
        // 这个函数的作用是提供 task-service 的基础地址，供摘要聚合时查询待办任务。
        return taskServiceBaseUrl;
    }

    public void setTaskServiceBaseUrl(String taskServiceBaseUrl) {
        // 这个函数的作用是允许通过配置文件覆盖 task-service 地址，方便本地联调和部署切换。
        this.taskServiceBaseUrl = taskServiceBaseUrl;
    }

    public String getScheduleServiceBaseUrl() {
        // 这个函数的作用是提供 schedule-service 的基础地址，供摘要聚合时查询日程安排。
        return scheduleServiceBaseUrl;
    }

    public void setScheduleServiceBaseUrl(String scheduleServiceBaseUrl) {
        // 这个函数的作用是允许通过配置文件覆盖 schedule-service 地址，方便多环境部署。
        this.scheduleServiceBaseUrl = scheduleServiceBaseUrl;
    }

    public String getQqConnectorBaseUrl() {
        // 这个函数的作用是提供 QQ/NapCat Connector 的内部地址，供工作台确认草稿后发送消息。
        return qqConnectorBaseUrl;
    }

    public void setQqConnectorBaseUrl(String qqConnectorBaseUrl) {
        // 这个函数的作用是允许不同环境替换 QQ Connector 地址，避免把本地端口写死在业务代码中。
        this.qqConnectorBaseUrl = qqConnectorBaseUrl;
    }
}
