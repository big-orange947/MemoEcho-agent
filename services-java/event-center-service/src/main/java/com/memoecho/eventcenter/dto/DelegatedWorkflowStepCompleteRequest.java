package com.memoecho.eventcenter.dto;

import java.util.List;
import java.util.Map;

/**
 * Runtime 完成父工作流步骤时提交的结构化结果。
 * producedFacts 只允许包含规划阶段声明的事实，result 保存步骤的业务结果原文。
 *
 * <p>artifacts 是类型化产物的正式表达：每个 artifact 的 name 必须属于该步骤声明的
 * producesFacts，type 用于表达产物语义（如 CLASS_TIME），sourceEventId 记录产出来源事件。
 * 当 artifacts 存在时，Java 以 artifacts 为准生成事实映射并原子持久化。
 *
 * <p>sourceEventId 携带触发本次完成的事件 ID。后续被解锁激活的步骤会把它作为
 * 起点水位（startEventId），从而让 L1 历史读取只覆盖起点之后的证据。
 */
public record DelegatedWorkflowStepCompleteRequest(
        List<DelegatedWorkflowArtifactRequest> artifacts,
        Map<String, Object> producedFacts,
        String resultSummary,
        Object result,
        String sourceEventId
) {
}
