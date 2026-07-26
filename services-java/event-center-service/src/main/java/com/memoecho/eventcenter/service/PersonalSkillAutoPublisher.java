package com.memoecho.eventcenter.service;

import com.fasterxml.jackson.databind.JsonNode;
import com.fasterxml.jackson.databind.ObjectMapper;
import com.fasterxml.jackson.databind.node.ArrayNode;
import com.fasterxml.jackson.databind.node.ObjectNode;
import com.memoecho.eventcenter.config.SkillStoreProperties;
import com.memoecho.eventcenter.model.ConversationProfile;
import com.memoecho.eventcenter.model.StoredEvent;
import com.memoecho.eventcenter.repository.ConversationProfileRepository;
import com.memoecho.eventcenter.repository.EventRecordRepository;
import com.memoecho.eventcenter.service.PersonalStyleAnalyzer.ConfidenceBreakdown;
import com.memoecho.eventcenter.service.PersonalStyleAnalyzer.PersonalStyleAnalysis;
import com.memoecho.eventcenter.service.PersonalStyleAnalyzer.StyleMetrics;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.beans.factory.annotation.Value;
import org.springframework.scheduling.annotation.Scheduled;
import org.springframework.stereotype.Service;

import java.io.IOException;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.nio.file.Path;
import java.security.MessageDigest;
import java.security.NoSuchAlgorithmException;
import java.time.Instant;
import java.util.ArrayList;
import java.util.Comparator;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Locale;
import java.util.Map;

/**
 * 评估用户授权的本人消息样本，并发布可选、可解释、可回滚的个人表达 Skill。
 *
 * <p>发布结果只包含表达风格统计和提示词，不会把原始聊天记录、联系人或历史事实
 * 写入 Skill。Skill 达到成熟度后只会出现在可选列表中，不会自动绑定到会话。</p>
 */
@Service
public class PersonalSkillAutoPublisher {

    private static final Logger log = LoggerFactory.getLogger(PersonalSkillAutoPublisher.class);
    private final ConversationProfileRepository profileRepository;
    private final EventRecordRepository eventRepository;
    private final SkillStoreProperties skillStoreProperties;
    private final ObjectMapper objectMapper;
    private final PersonalStyleAnalyzer styleAnalyzer;
    private final int minSampleCount;
    private final double minConfidence;
    private final int minHistorySpanDays;

    /**
     * 注入样本仓库、Skill 存储和成熟度阈值。
     */
    public PersonalSkillAutoPublisher(
            ConversationProfileRepository profileRepository,
            EventRecordRepository eventRepository,
            SkillStoreProperties skillStoreProperties,
            ObjectMapper objectMapper,
            PersonalStyleAnalyzer styleAnalyzer,
            @Value("${event-center.skills.personal-min-samples:500}") int minSampleCount,
            @Value("${event-center.skills.personal-min-confidence:0.80}") double minConfidence,
            @Value("${event-center.skills.personal-min-history-span-days:30}") int minHistorySpanDays
    ) {
        this.profileRepository = profileRepository;
        this.eventRepository = eventRepository;
        this.skillStoreProperties = skillStoreProperties;
        this.objectMapper = objectMapper;
        this.styleAnalyzer = styleAnalyzer;
        this.minSampleCount = Math.max(100, minSampleCount);
        this.minConfidence = Math.max(0.5, Math.min(1.0, minConfidence));
        this.minHistorySpanDays = Math.max(1, minHistorySpanDays);
    }

    /**
     * 定期评估所有已授权的私聊设定，使新增的人工消息可以逐步养成个人 Skill。
     */
    @Scheduled(fixedDelayString = "${event-center.skills.personal-evaluation-interval-ms:60000}")
    public void evaluateAllAuthorizedProfiles() {
        profileRepository.findAll().stream()
                .filter(ConversationProfile::enabled)
                .filter(ConversationProfile::historyTrainingEnabled)
                .forEach(profile -> {
                    try {
                        evaluate(profile);
                    } catch (RuntimeException exception) {
                        log.warn("个人 Skill 自动评估失败，profileId={}：{}", profile.id(), exception.getMessage());
                    }
                });
    }

    /**
     * 评估单个设定绑定的会话风格；样本量、时间跨度和综合可信度必须同时达标。
     */
    public PublicationResult evaluate(ConversationProfile profile) {
        List<StoredEvent> userEvents = eventRepository.findAll().stream()
                .filter(event -> profile.userId().equals(event.ownerUserId()))
                .filter(event -> event.payload() != null)
                .toList();
        List<ConversationProfile> authorizedProfiles = findAuthorizedProfiles(profile);
        List<StoredEvent> globalEvents = userEvents.stream()
                .filter(event -> authorizedProfiles.stream().anyMatch(scope -> matchesProfileScope(event, scope)))
                .toList();
        List<StoredEvent> modeEvents = globalEvents.stream()
                .filter(event -> sameText(event.payload().platform(), profile.platform()))
                .filter(event -> sameText(event.payload().chatType(), profile.chatType()))
                .toList();
        List<StoredEvent> profileEvents = userEvents.stream()
                .filter(event -> matchesProfileScope(event, profile))
                .toList();

        StyleHierarchyAnalysis hierarchy = new StyleHierarchyAnalysis(
                styleAnalyzer.analyze(globalEvents, minSampleCount, minHistorySpanDays),
                styleAnalyzer.analyze(modeEvents, minSampleCount, minHistorySpanDays),
                styleAnalyzer.analyze(profileEvents, minSampleCount, minHistorySpanDays),
                ""
        );
        hierarchy = hierarchy.withFingerprint(buildHierarchyFingerprint(hierarchy));
        PersonalStyleAnalysis analysis = hierarchy.profile();
        if (!isMature(analysis)) {
            return new PublicationResult(
                    false,
                    "",
                    analysis.samples().size(),
                    analysis.confidence()
            );
        }

        String reference = "personal/" + safeSegment(profile.userId()) + "/" + safeSegment(profile.id());
        writeDescriptor(profile, reference, hierarchy);
        return new PublicationResult(true, reference, analysis.samples().size(), analysis.confidence());
    }

    /**
     * 收集当前用户明确允许用于训练的设定集，并始终包含本次正在评估的设定。
     * 这样全局和会话模式基线不会读取未授权会话，也兼容历史同步完成后立即评估的场景。
     */
    private List<ConversationProfile> findAuthorizedProfiles(ConversationProfile currentProfile) {
        Map<String, ConversationProfile> profiles = new LinkedHashMap<>();
        List<ConversationProfile> storedProfiles = profileRepository.findAll();
        if (storedProfiles != null) {
            storedProfiles.stream()
                    .filter(profile -> profile.userId().equals(currentProfile.userId()))
                    .filter(ConversationProfile::enabled)
                    .filter(ConversationProfile::historyTrainingEnabled)
                    .forEach(profile -> profiles.put(profile.id(), profile));
        }
        profiles.put(currentProfile.id(), currentProfile);
        return List.copyOf(profiles.values());
    }

    /**
     * 判断事件是否属于指定设定集的训练范围，同时校验平台、会话类型和会话 ID。
     */
    private boolean matchesProfileScope(StoredEvent event, ConversationProfile profile) {
        return event != null
                && event.payload() != null
                && sameText(event.payload().platform(), profile.platform())
                && sameText(event.payload().chatType(), profile.chatType())
                && profile.chatIds() != null
                && profile.chatIds().contains(event.payload().chatId());
    }

    /**
     * 对平台和会话类型做不区分大小写比较，避免旧数据大小写差异拆散样本。
     */
    private boolean sameText(String first, String second) {
        return first != null && second != null && first.equalsIgnoreCase(second);
    }

    /**
     * 统一判断某层样本是否达到数量、时间跨度和综合可信度三项发布门槛。
     */
    private boolean isMature(PersonalStyleAnalysis analysis) {
        return analysis.samples().size() >= minSampleCount
                && analysis.historySpanDays() >= minHistorySpanDays
                && analysis.confidence() >= minConfidence;
    }

    /**
     * 把三层样本指纹再次哈希，任意一层发生变化都会生成新的可回滚 Skill 版本。
     */
    private String buildHierarchyFingerprint(StyleHierarchyAnalysis hierarchy) {
        try {
            MessageDigest digest = MessageDigest.getInstance("SHA-256");
            digest.update(hierarchy.global().fingerprint().getBytes(StandardCharsets.UTF_8));
            digest.update((byte) 0);
            digest.update(hierarchy.mode().fingerprint().getBytes(StandardCharsets.UTF_8));
            digest.update((byte) 0);
            digest.update(hierarchy.profile().fingerprint().getBytes(StandardCharsets.UTF_8));
            return java.util.HexFormat.of().formatHex(digest.digest());
        } catch (NoSuchAlgorithmException exception) {
            throw new IllegalStateException("当前 Java 环境不支持 SHA-256", exception);
        }
    }

    /**
     * 写入当前描述符、不可变历史版本和便于用户阅读的 SKILL.md。
     */
    private void writeDescriptor(
            ConversationProfile profile,
            String reference,
            StyleHierarchyAnalysis hierarchy
    ) {
        Path directory = Path.of(skillStoreProperties.getInstalledRoot())
                .toAbsolutePath()
                .normalize()
                .resolve(reference);
        Path descriptorPath = directory.resolve("skill.json");
        if (hasSameFingerprint(descriptorPath, hierarchy.fingerprint())) {
            return;
        }

        ObjectNode descriptor = buildDescriptor(profile, reference, hierarchy);
        String versionId = shortFingerprint(hierarchy.fingerprint());
        try {
            Files.createDirectories(directory.resolve("versions"));
            String descriptorJson = objectMapper.writerWithDefaultPrettyPrinter().writeValueAsString(descriptor);
            Files.writeString(descriptorPath, descriptorJson, StandardCharsets.UTF_8);
            Files.writeString(
                    directory.resolve("versions").resolve(versionId + ".json"),
                    descriptorJson,
                    StandardCharsets.UTF_8
            );
            Files.writeString(
                    directory.resolve("SKILL.md"),
                    buildHumanReadableSkill(profile, hierarchy),
                    StandardCharsets.UTF_8
            );
        } catch (IOException exception) {
            throw new IllegalStateException("写入个人 Skill 失败", exception);
        }
    }

    /**
     * 构造 Runtime 可解析的 Skill 描述符，并附加成熟度和风格统计供前端解释。
     */
    private ObjectNode buildDescriptor(
            ConversationProfile profile,
            String reference,
            StyleHierarchyAnalysis hierarchy
    ) {
        PersonalStyleAnalysis analysis = hierarchy.profile();
        StyleMetrics metrics = analysis.metrics();
        ObjectNode descriptor = objectMapper.createObjectNode();
        descriptor.put("id", "personal." + safeSegment(profile.userId()) + "." + safeSegment(profile.id()));
        descriptor.put("name", "我的表达风格 · " + profile.name());
        descriptor.put("version", "1.1.0+" + shortFingerprint(hierarchy.fingerprint()));
        descriptor.put("type", "persona");
        descriptor.put("description", "根据用户授权的本人消息分层提炼，仅用于模拟当前设定、会话模式和全局稳定表达习惯。");
        descriptor.put("source", "personal");
        descriptor.put("rawReference", reference);
        descriptor.put("ownerUserId", profile.userId());
        descriptor.put("sampleFingerprint", hierarchy.fingerprint());
        descriptor.put("profileSampleFingerprint", analysis.fingerprint());
        descriptor.put("generatedAt", Instant.now().toString());
        descriptor.putArray("applicableRoutes").add("social_reply");

        ObjectNode scope = descriptor.putObject("scope");
        scope.put("kind", "conversation_profile");
        scope.put("profileId", profile.id());
        scope.put("profileName", profile.name());
        scope.put("platform", profile.platform());
        scope.put("chatType", profile.chatType());
        scope.put("boundConversationCount", profile.chatIds().size());

        ObjectNode maturity = descriptor.putObject("maturity");
        maturity.put("status", "AVAILABLE");
        maturity.put("sampleCount", analysis.samples().size());
        maturity.put("observedOwnMessages", analysis.observedOwnMessages());
        maturity.put("discardedMessages", analysis.discardedMessages());
        maturity.put("activeDays", analysis.activeDays());
        maturity.put("historySpanDays", analysis.historySpanDays());
        maturity.put("confidence", analysis.confidence());
        appendConfidenceBreakdown(maturity.putObject("confidenceBreakdown"), analysis.confidenceBreakdown());

        appendStyleMetrics(descriptor.putObject("styleProfile"), metrics);
        appendStyleHierarchy(descriptor.putObject("styleHierarchy"), profile, hierarchy);

        ObjectNode trainingPolicy = descriptor.putObject("trainingPolicy");
        trainingPolicy.put("selfAuthoredOnly", true);
        trainingPolicy.put("rawMessagesEmbedded", false);
        trainingPolicy.putArray("allowedOrigins").add("USER_MANUAL").add("HISTORY_CONSENTED");
        trainingPolicy.putArray("excludedOrigins").add("AGENT_AUTO").add("AGENT_CONFIRMED").add("EXTERNAL");

        descriptor.putObject("promptFragments").put("system", buildStylePrompt(profile, hierarchy));
        descriptor.putObject("toolPolicy").putArray("allow");
        descriptor.putObject("modelHints").put("temperature", 0.7).put("maxTokens", 256);
        return descriptor;
    }

    /**
     * 把某一层的可解释风格指标写入 JSON，不包含任何原始消息或历史事实。
     */
    private void appendStyleMetrics(ObjectNode target, StyleMetrics metrics) {
        target.put("averageLength", metrics.averageLength());
        target.put("medianLength", metrics.medianLength());
        target.put("p90Length", metrics.p90Length());
        target.put("shortReplyRate", metrics.shortReplyRate());
        target.put("terminalPunctuationRate", metrics.terminalPunctuationRate());
        target.put("punctuationDensity", metrics.punctuationDensity());
        target.put("questionRate", metrics.questionRate());
        target.put("emojiRate", metrics.emojiRate());
        target.put("multilineRate", metrics.multilineRate());
        ArrayNode particles = target.putArray("commonEndingParticles");
        metrics.commonEndingParticles().forEach(particles::add);
    }

    /**
     * 写入全局、平台会话模式和当前设定三层成熟度，供客户端解释 Skill 的证据来源。
     */
    private void appendStyleHierarchy(
            ObjectNode target,
            ConversationProfile profile,
            StyleHierarchyAnalysis hierarchy
    ) {
        target.put("strategy", "PROFILE_OVER_MODE_OVER_GLOBAL");
        target.put("description", "当前设定优先；当前层没有稳定证据时，依次参考同类会话模式和全局基线。");
        ArrayNode layers = target.putArray("layers");
        appendHierarchyLayer(layers, "global", "全部已授权会话", hierarchy.global());
        appendHierarchyLayer(
                layers,
                "mode",
                profile.platform().toLowerCase(Locale.ROOT) + ":" + profile.chatType().toLowerCase(Locale.ROOT),
                hierarchy.mode()
        );
        appendHierarchyLayer(layers, "profile", profile.id(), hierarchy.profile());
    }

    /**
     * 写入单层样本量、时间覆盖、可信度与风格指标，避免前端只能看到一个不透明总分。
     */
    private void appendHierarchyLayer(
            ArrayNode layers,
            String layerName,
            String scopeKey,
            PersonalStyleAnalysis analysis
    ) {
        ObjectNode layer = layers.addObject();
        layer.put("layer", layerName);
        layer.put("scopeKey", scopeKey);
        layer.put("sampleCount", analysis.samples().size());
        layer.put("activeDays", analysis.activeDays());
        layer.put("historySpanDays", analysis.historySpanDays());
        layer.put("confidence", analysis.confidence());
        layer.put("mature", isMature(analysis));
        appendConfidenceBreakdown(layer.putObject("confidenceBreakdown"), analysis.confidenceBreakdown());
        appendStyleMetrics(layer.putObject("metrics"), analysis.metrics());
    }

    /**
     * 把可信度分项复制到 JSON，便于客户端展示成熟度原因。
     */
    private void appendConfidenceBreakdown(ObjectNode target, ConfidenceBreakdown breakdown) {
        target.put("volume", breakdown.volume());
        target.put("dataQuality", breakdown.dataQuality());
        target.put("temporalCoverage", breakdown.temporalCoverage());
        target.put("styleStability", breakdown.styleStability());
    }

    /**
     * 根据统计指标生成紧凑的风格提示词，明确禁止从历史样本继承事实。
     */
    private String buildStylePrompt(ConversationProfile profile, StyleHierarchyAnalysis hierarchy) {
        StyleMetrics metrics = hierarchy.profile().metrics();
        List<String> rules = new ArrayList<>();
        rules.add("这是用户在会话模式「" + profile.name() + "」下的个人表达风格，只模仿表达形式。");
        rules.add("不得把历史消息中的身份、关系、经历、联系方式、价格或其他事实当成当前事实。");
        rules.add("只输出可直接发送的聊天正文，不解释风格，不自称助手，不使用 Markdown。");
        rules.add("风格证据按当前设定、" + profile.platform() + " " + profile.chatType()
                + " 会话模式、用户全局基线的顺序使用；发生冲突时必须以当前设定为准。");
        rules.add("常见单条消息约 " + Math.max(2, metrics.medianLength())
                + " 个字，通常不要超过 " + Math.max(metrics.medianLength(), metrics.p90Length()) + " 个字。");

        if (metrics.shortReplyRate() >= 0.60) {
            rules.add("优先使用自然短句；内容较多时可以拆成少量连续消息，不要写成长段说明。");
        }
        if (metrics.terminalPunctuationRate() <= 0.15) {
            rules.add("普通聊天短句末尾通常不加标点，只有长句或语义确实需要时才使用。");
        } else if (metrics.terminalPunctuationRate() >= 0.65) {
            rules.add("保留自然句末标点，但不要连续使用感叹号或问号。");
        } else {
            rules.add("标点保持口语化，短句末尾可以省略，避免过度书面化。");
        }
        if (metrics.emojiRate() <= 0.05) {
            rules.add("不要为了模仿风格主动添加 emoji 或颜文字。");
        } else if (metrics.emojiRate() >= 0.25) {
            rules.add("仅在语境自然时偶尔使用一个表情，不要堆叠表情。");
        }
        if (metrics.questionRate() >= 0.35) {
            rules.add("需要补充信息时可以简短追问，但不能为了模仿而重复提问。");
        }
        if (!metrics.commonEndingParticles().isEmpty()) {
            rules.add("常见语气词包括「" + String.join("、", metrics.commonEndingParticles())
                    + "」，只能在语境合适时偶尔使用，不能每句套用。");
        }
        buildHierarchyDifferences(hierarchy).forEach(rules::add);
        return String.join("\n", rules);
    }

    /**
     * 比较当前设定与上层基线，只保留最明显的差异，防止提示词堆叠大量统计数字。
     */
    private List<String> buildHierarchyDifferences(StyleHierarchyAnalysis hierarchy) {
        List<MetricDifference> differences = new ArrayList<>();
        differences.addAll(compareMetrics(
                hierarchy.mode().metrics(),
                hierarchy.global().metrics(),
                "同类会话相对全局"
        ));
        differences.addAll(compareMetrics(
                hierarchy.profile().metrics(),
                hierarchy.mode().metrics(),
                "当前设定相对同类会话"
        ));
        return differences.stream()
                .sorted(Comparator.comparingDouble(MetricDifference::score).reversed())
                .limit(2)
                .map(MetricDifference::instruction)
                .toList();
    }

    /**
     * 将两层风格的显著差异转换成自然语言约束，微小波动不会进入最终提示词。
     */
    private List<MetricDifference> compareMetrics(
            StyleMetrics child,
            StyleMetrics parent,
            String label
    ) {
        List<MetricDifference> differences = new ArrayList<>();
        double lengthDelta = parent.medianLength() == 0
                ? 0.0
                : (double) (child.medianLength() - parent.medianLength()) / parent.medianLength();
        if (Math.abs(lengthDelta) >= 0.25 && Math.abs(child.medianLength() - parent.medianLength()) >= 3) {
            differences.add(new MetricDifference(
                    Math.abs(lengthDelta),
                    label + (lengthDelta < 0 ? "更偏向短句，避免被全局长句习惯拉长。" : "允许稍长表达，但仍服从当前单条长度上限。")
            ));
        }
        addRateDifference(
                differences,
                child.shortReplyRate() - parent.shortReplyRate(),
                0.15,
                label + "更常使用短回复。",
                label + "较少只用极短回复。"
        );
        addRateDifference(
                differences,
                child.terminalPunctuationRate() - parent.terminalPunctuationRate(),
                0.15,
                label + "更常保留句末标点。",
                label + "更常省略句末标点。"
        );
        addRateDifference(
                differences,
                child.emojiRate() - parent.emojiRate(),
                0.12,
                label + "更常使用表情，但仍需符合当前语境。",
                label + "更少使用表情，不要从上层风格补入。"
        );
        addRateDifference(
                differences,
                child.questionRate() - parent.questionRate(),
                0.15,
                label + "更常用简短追问推进对话。",
                label + "较少连续追问。"
        );
        return differences;
    }

    /**
     * 仅当两个比例差异超过阈值时添加约束，并以差值绝对值作为排序分数。
     */
    private void addRateDifference(
            List<MetricDifference> differences,
            double delta,
            double threshold,
            String positiveInstruction,
            String negativeInstruction
    ) {
        if (Math.abs(delta) >= threshold) {
            differences.add(new MetricDifference(
                    Math.abs(delta),
                    delta > 0 ? positiveInstruction : negativeInstruction
            ));
        }
    }

    /**
     * 生成不含原始消息的人类可读说明，方便用户检查个人 Skill 学到了什么。
     */
    private String buildHumanReadableSkill(ConversationProfile profile, StyleHierarchyAnalysis hierarchy) {
        PersonalStyleAnalysis analysis = hierarchy.profile();
        StyleMetrics metrics = analysis.metrics();
        return """
                ---
                name: %s
                description: 根据用户授权的本人消息提炼的会话表达风格
                ---

                # 适用范围

                - 平台：%s
                - 会话类型：%s
                - 会话模式：%s

                # 分层证据

                - 全局基线：%d 条，可信度 %.3f
                - 同类会话：%d 条，可信度 %.3f
                - 当前设定：%d 条，可信度 %.3f
                - 使用优先级：当前设定 > 同类会话 > 全局基线

                # 成熟度

                - 有效样本：%d
                - 历史跨度：%d 天
                - 活跃日期：%d 天
                - 综合可信度：%.3f

                # 表达特征

                - 中位长度：%d 字
                - P90 长度：%d 字
                - 短回复比例：%.3f
                - 句末标点比例：%.3f
                - 表情比例：%.3f

                # 使用约束

                该 Skill 只控制表达形式，不提供事实来源，也不会包含原始聊天记录。
                """.formatted(
                "我的表达风格 · " + profile.name(),
                profile.platform(),
                profile.chatType(),
                profile.name(),
                hierarchy.global().samples().size(),
                hierarchy.global().confidence(),
                hierarchy.mode().samples().size(),
                hierarchy.mode().confidence(),
                hierarchy.profile().samples().size(),
                hierarchy.profile().confidence(),
                analysis.samples().size(),
                analysis.historySpanDays(),
                analysis.activeDays(),
                analysis.confidence(),
                metrics.medianLength(),
                metrics.p90Length(),
                metrics.shortReplyRate(),
                metrics.terminalPunctuationRate(),
                metrics.emojiRate()
        );
    }

    /**
     * 检查当前描述符是否已经来自同一批样本，防止定时任务重复生成版本。
     */
    private boolean hasSameFingerprint(Path descriptorPath, String fingerprint) {
        if (!Files.exists(descriptorPath)) {
            return false;
        }
        try {
            JsonNode current = objectMapper.readTree(Files.readString(descriptorPath, StandardCharsets.UTF_8));
            return fingerprint.equals(current.path("sampleFingerprint").asText(""));
        } catch (IOException exception) {
            log.warn("读取现有个人 Skill 失败，将重新生成：{}", exception.getMessage());
            return false;
        }
    }

    /**
     * 生成适合目录和 Skill ID 使用的安全片段。
     */
    private String safeSegment(String value) {
        return String.valueOf(value).toLowerCase(Locale.ROOT).replaceAll("[^a-z0-9_-]", "_");
    }

    /**
     * 截取样本指纹作为可读版本标识。
     */
    private String shortFingerprint(String fingerprint) {
        return fingerprint == null || fingerprint.length() < 12 ? "unknown" : fingerprint.substring(0, 12);
    }

    /**
     * 返回发布状态和成熟度，供历史同步接口直接展示。
     */
    public record PublicationResult(boolean published, String reference, int sampleCount, double confidence) {
    }

    /**
     * 保存全局、同类会话和当前设定三层分析结果；原始样本仅在内存中参与统计和指纹计算。
     */
    private record StyleHierarchyAnalysis(
            PersonalStyleAnalysis global,
            PersonalStyleAnalysis mode,
            PersonalStyleAnalysis profile,
            String fingerprint
    ) {
        /**
         * 返回写入组合指纹后的不可变副本。
         */
        private StyleHierarchyAnalysis withFingerprint(String value) {
            return new StyleHierarchyAnalysis(global, mode, profile, value);
        }
    }

    /**
     * 表示一条按显著程度排序的层间风格差异。
     */
    private record MetricDifference(double score, String instruction) {
    }
}
