package com.memoecho.eventcenter.config;

import org.springframework.boot.context.properties.ConfigurationProperties;

@ConfigurationProperties(prefix = "event-center.dispatch.agent-runtime")
public class AgentRuntimeDispatchProperties {

    private boolean enabled = true;
    private String baseUrl = "http://127.0.0.1:8000";
    private String handlePath = "/v1/events/handle";

    public boolean isEnabled() {
        return enabled;
    }

    public void setEnabled(boolean enabled) {
        this.enabled = enabled;
    }

    public String getBaseUrl() {
        return baseUrl;
    }

    public void setBaseUrl(String baseUrl) {
        this.baseUrl = baseUrl;
    }

    public String getHandlePath() {
        return handlePath;
    }

    public void setHandlePath(String handlePath) {
        this.handlePath = handlePath;
    }
}
