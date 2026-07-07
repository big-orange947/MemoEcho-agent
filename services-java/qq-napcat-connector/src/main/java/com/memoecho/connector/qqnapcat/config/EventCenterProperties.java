package com.memoecho.connector.qqnapcat.config;

import org.springframework.boot.context.properties.ConfigurationProperties;

@ConfigurationProperties(prefix = "connector.event-center")
public class EventCenterProperties {

    private boolean forwardEnabled = true;
    private String baseUrl = "http://127.0.0.1:8093";
    private String ingestPath = "/internal/events/ingest";

    public boolean isForwardEnabled() {
        return forwardEnabled;
    }

    public void setForwardEnabled(boolean forwardEnabled) {
        this.forwardEnabled = forwardEnabled;
    }

    public String getBaseUrl() {
        return baseUrl;
    }

    public void setBaseUrl(String baseUrl) {
        this.baseUrl = baseUrl;
    }

    public String getIngestPath() {
        return ingestPath;
    }

    public void setIngestPath(String ingestPath) {
        this.ingestPath = ingestPath;
    }
}
