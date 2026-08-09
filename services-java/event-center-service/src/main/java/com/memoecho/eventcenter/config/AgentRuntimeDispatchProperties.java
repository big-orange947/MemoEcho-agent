package com.memoecho.eventcenter.config;

import org.springframework.boot.context.properties.ConfigurationProperties;

@ConfigurationProperties(prefix = "event-center.dispatch.agent-runtime")
public class AgentRuntimeDispatchProperties {

    private boolean enabled = true;
    private String baseUrl = "http://127.0.0.1:8000";
    private String handlePath = "/v1/events/handle";
    private String progressPath = "/v1/conversations/progress";
    private String cognitionPath = "/v1/conversations/cognition";
    private String pendingGroupOperationPath = "/v1/group-operations/pending";
    private String approveGroupOperationPath = "/v1/group-operations/approve-event";
    private String delegatedTaskCompilePath = "/v1/delegated-tasks/compile";
    private String delegatedWorkflowStepExecutePath = "/v1/delegated-workflows/steps/execute";

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

    public String getProgressPath() {
        return progressPath;
    }

    public void setProgressPath(String progressPath) {
        this.progressPath = progressPath;
    }

    public String getCognitionPath() {
        return cognitionPath;
    }

    public void setCognitionPath(String cognitionPath) {
        this.cognitionPath = cognitionPath;
    }

    public String getPendingGroupOperationPath() {
        return pendingGroupOperationPath;
    }

    public void setPendingGroupOperationPath(String pendingGroupOperationPath) {
        this.pendingGroupOperationPath = pendingGroupOperationPath;
    }

    public String getApproveGroupOperationPath() {
        return approveGroupOperationPath;
    }

    public void setApproveGroupOperationPath(String approveGroupOperationPath) {
        this.approveGroupOperationPath = approveGroupOperationPath;
    }

    public String getDelegatedTaskCompilePath() {
        return delegatedTaskCompilePath;
    }

    public void setDelegatedTaskCompilePath(String delegatedTaskCompilePath) {
        this.delegatedTaskCompilePath = delegatedTaskCompilePath;
    }

    public String getDelegatedWorkflowStepExecutePath() {
        return delegatedWorkflowStepExecutePath;
    }

    public void setDelegatedWorkflowStepExecutePath(String delegatedWorkflowStepExecutePath) {
        this.delegatedWorkflowStepExecutePath = delegatedWorkflowStepExecutePath;
    }
}
