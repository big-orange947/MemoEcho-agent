package com.memoecho.connector.qqnapcat.config;

import org.springframework.boot.context.properties.ConfigurationProperties;

/**
 * 保存 Memo Echo 托管 NapCat WebUI 时使用的本机参数。
 * WebUI Token 只在 Connector 进程内使用，不会返回给桌面客户端。
 */
@ConfigurationProperties(prefix = "napcat.webui")
public class NapcatWebUiProperties {

    private boolean enabled = true;
    private String baseUrl = "http://127.0.0.1:6099";
    private String apiPrefix = "/api";
    private String token = "";
    private String dockerContainers = "memo_echo_napcat,napcat";
    private String nativeConfigPaths = "";
    private String managedRuntimeRoot = "";
    private String eventCallbackUrl = "";
    private int onebotHttpPort = 3011;

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

    public String getApiPrefix() {
        return apiPrefix;
    }

    public void setApiPrefix(String apiPrefix) {
        this.apiPrefix = apiPrefix;
    }

    public String getToken() {
        return token;
    }

    public void setToken(String token) {
        this.token = token;
    }

    public String getDockerContainers() {
        return dockerContainers;
    }

    public void setDockerContainers(String dockerContainers) {
        this.dockerContainers = dockerContainers;
    }

    public String getNativeConfigPaths() {
        return nativeConfigPaths;
    }

    public void setNativeConfigPaths(String nativeConfigPaths) {
        this.nativeConfigPaths = nativeConfigPaths;
    }

    public String getManagedRuntimeRoot() {
        return managedRuntimeRoot;
    }

    public void setManagedRuntimeRoot(String managedRuntimeRoot) {
        this.managedRuntimeRoot = managedRuntimeRoot;
    }

    public String getEventCallbackUrl() {
        return eventCallbackUrl;
    }

    public void setEventCallbackUrl(String eventCallbackUrl) {
        this.eventCallbackUrl = eventCallbackUrl;
    }

    public int getOnebotHttpPort() {
        return onebotHttpPort;
    }

    public void setOnebotHttpPort(int onebotHttpPort) {
        this.onebotHttpPort = onebotHttpPort;
    }
}
