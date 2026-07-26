package com.memoecho.eventcenter.config;

import org.springframework.boot.context.properties.ConfigurationProperties;

@ConfigurationProperties(prefix = "event-center.dispatch.retry")
public class AgentDispatchRetryProperties {

    private boolean enabled = true;
    private int maxAttempts = 4;
    private long initialDelaySeconds = 3;
    private long maxDelaySeconds = 60;
    private long pollIntervalMs = 1000;
    private int batchSize = 20;

    /** 返回是否启用 Runtime 自动重试。 */
    public boolean isEnabled() {
        return enabled;
    }

    /** 设置是否启用 Runtime 自动重试。 */
    public void setEnabled(boolean enabled) {
        this.enabled = enabled;
    }

    /** 返回包含首次请求在内的最大尝试次数。 */
    public int getMaxAttempts() {
        return maxAttempts;
    }

    /** 设置包含首次请求在内的最大尝试次数。 */
    public void setMaxAttempts(int maxAttempts) {
        this.maxAttempts = Math.max(maxAttempts, 1);
    }

    /** 返回第一次自动重试前的等待秒数。 */
    public long getInitialDelaySeconds() {
        return initialDelaySeconds;
    }

    /** 设置第一次自动重试前的等待秒数。 */
    public void setInitialDelaySeconds(long initialDelaySeconds) {
        this.initialDelaySeconds = Math.max(initialDelaySeconds, 0);
    }

    /** 返回指数退避允许的最大等待秒数。 */
    public long getMaxDelaySeconds() {
        return maxDelaySeconds;
    }

    /** 设置指数退避允许的最大等待秒数。 */
    public void setMaxDelaySeconds(long maxDelaySeconds) {
        this.maxDelaySeconds = Math.max(maxDelaySeconds, 0);
    }

    /** 返回重试任务扫描间隔。 */
    public long getPollIntervalMs() {
        return pollIntervalMs;
    }

    /** 设置重试任务扫描间隔。 */
    public void setPollIntervalMs(long pollIntervalMs) {
        this.pollIntervalMs = Math.max(pollIntervalMs, 250);
    }

    /** 返回每次扫描最多领取的任务数量。 */
    public int getBatchSize() {
        return batchSize;
    }

    /** 设置每次扫描最多领取的任务数量。 */
    public void setBatchSize(int batchSize) {
        this.batchSize = Math.max(batchSize, 1);
    }
}
