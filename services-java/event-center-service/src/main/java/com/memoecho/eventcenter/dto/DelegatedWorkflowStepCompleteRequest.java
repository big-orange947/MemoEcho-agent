package com.memoecho.eventcenter.dto;

import java.util.Map;

/**
 * Runtime 完成父工作流步骤时提交的结构化结果。
 * producedFacts 只允许包含规划阶段声明的事实，result 保存步骤的业务结果原文。
 */
public record DelegatedWorkflowStepCompleteRequest(
        Map<String, Object> producedFacts,
        String resultSummary,
        Object result
) {
}
