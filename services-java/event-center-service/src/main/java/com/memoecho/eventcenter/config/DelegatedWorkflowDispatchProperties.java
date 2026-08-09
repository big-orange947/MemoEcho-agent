package com.memoecho.eventcenter.config;

import org.springframework.boot.context.properties.ConfigurationProperties;

/** 配置委托工作流步骤的可靠投递、租约和指数退避。 */
@ConfigurationProperties(prefix = "event-center.delegated-workflow-dispatch")
public class DelegatedWorkflowDispatchProperties {
    private boolean enabled = true;
    private long pollIntervalMs = 1000;
    private int batchSize = 20;
    private long leaseSeconds = 60;
    private long initialRetrySeconds = 2;
    private long maxRetrySeconds = 60;

    public boolean isEnabled() { return enabled; }
    public void setEnabled(boolean enabled) { this.enabled = enabled; }
    public long getPollIntervalMs() { return pollIntervalMs; }
    public void setPollIntervalMs(long pollIntervalMs) { this.pollIntervalMs = pollIntervalMs; }
    public int getBatchSize() { return batchSize; }
    public void setBatchSize(int batchSize) { this.batchSize = batchSize; }
    public long getLeaseSeconds() { return leaseSeconds; }
    public void setLeaseSeconds(long leaseSeconds) { this.leaseSeconds = leaseSeconds; }
    public long getInitialRetrySeconds() { return initialRetrySeconds; }
    public void setInitialRetrySeconds(long initialRetrySeconds) { this.initialRetrySeconds = initialRetrySeconds; }
    public long getMaxRetrySeconds() { return maxRetrySeconds; }
    public void setMaxRetrySeconds(long maxRetrySeconds) { this.maxRetrySeconds = maxRetrySeconds; }
}
