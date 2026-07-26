package com.memoecho.eventcenter.model;

import java.util.List;

/**
 * Conversation Profile 2.0 的结构化上下文。
 *
 * <p>这里只保存可用于推理的业务描述和资产引用，不保存收款码、卡密等敏感资产正文。
 * 工具权限、审批策略、知识来源和记忆策略继续使用 ConversationProfile 的既有字段。</p>
 */
public record ConversationProfileContext(
        int version,
        Identity identity,
        Counterparty counterparty,
        Background background,
        Task task,
        BusinessRules businessRules,
        MemoryPolicy memoryPolicy,
        List<AssetReference> assets
) {
    /** 返回不携带任何业务事实的 2.0 默认结构，兼容升级前的会话设定。 */
    public static ConversationProfileContext empty() {
        return new ConversationProfileContext(
                2,
                Identity.empty(),
                Counterparty.empty(),
                Background.empty(),
                Task.empty(),
                BusinessRules.empty(),
                MemoryPolicy.empty(),
                List.of()
        );
    }

    /** 描述 Agent 代表谁以及怎样表达；单条长度仍由 maxReplyChars 统一控制。 */
    public record Identity(
            String representedPerson,
            String role,
            String speakingStyle,
            List<String> forbiddenExpressions
    ) {
        /** 创建空身份信息，避免旧数据反序列化后出现 null 分支。 */
        public static Identity empty() {
            return new Identity("本人", "本人", "", List.of());
        }
    }

    /** 描述聊天对象的身份、关系、已知事实和沟通偏好。 */
    public record Counterparty(
            String name,
            String identity,
            String relationship,
            String preferredAddress,
            List<String> knownFacts,
            String trustLevel,
            String communicationPreference
    ) {
        /** 创建空对方资料，可信度默认未知。 */
        public static Counterparty empty() {
            return new Counterparty("", "", "", "", List.of(), "UNKNOWN", "");
        }
    }

    /** 描述会话起因、已经发生的事情和当前进展。 */
    public record Background(String origin, String previousEvents, String currentProgress) {
        /** 创建空会话背景。 */
        public static Background empty() {
            return new Background("", "", "");
        }
    }

    /** 描述本次代理任务、成功条件、期限和明确禁止的行为。 */
    public record Task(
            String objective,
            List<String> successCriteria,
            String deadline,
            List<String> prohibitedActions
    ) {
        /** 创建空会话任务。 */
        public static Task empty() {
            return new Task("", List.of(), "", List.of());
        }
    }

    /** 描述报价、退款、交付等业务规则；这些规则不自动授予任何工具权限。 */
    public record BusinessRules(
            String pricingPolicy,
            String minimumPrice,
            String refundPolicy,
            String deliveryConditions,
            List<String> hardConstraints
    ) {
        /** 创建空业务规则。 */
        public static BusinessRules empty() {
            return new BusinessRules("", "", "", "", List.of());
        }
    }

    /**
     * 描述当前会话是否允许提取长期记忆候选。
     *
     * <p>该授权与“读取历史”和“用于个人风格训练”相互独立。默认关闭，开启后也只允许
     * 从连接器明确标记为 OWNER 的真人消息中提取候选，候选仍需用户确认。</p>
     */
    public record MemoryPolicy(boolean extractionEnabled) {
        /** 创建默认关闭的记忆策略，保证旧设定升级后不会自动扩大数据用途。 */
        public static MemoryPolicy empty() {
            return new MemoryPolicy(false);
        }
    }

    /**
     * 可用资产的安全引用。
     * assetId 由后续资产仓库解析，Prompt 只能看到名称、类型和使用条件。
     */
    public record AssetReference(
            String assetId,
            String type,
            String name,
            String description,
            String usageCondition
    ) {
    }
}
