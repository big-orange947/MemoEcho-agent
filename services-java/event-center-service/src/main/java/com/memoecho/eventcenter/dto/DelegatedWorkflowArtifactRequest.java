package com.memoecho.eventcenter.dto;

import java.util.Map;

/**
 * Runtime 完成父工作流步骤时发布的类型化产物。
 *
 * <p>type 是产物类型（如 CLASS_TIME），name 必须是该步骤 planning 阶段声明的
 * producesFacts 之一，value 是结构化值，sourceEventId 指向产出来源事件。
 * Java 在同一事务内原子校验、保存产物、完成上游步骤并唤醒下游步骤。
 */
public record DelegatedWorkflowArtifactRequest(
        String type,
        String name,
        Object value,
        String sourceEventId
) {
}
