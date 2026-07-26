package com.memoecho.eventcenter.service;

import com.memoecho.eventcenter.model.StoredEvent;
import org.springframework.stereotype.Component;

import java.nio.charset.StandardCharsets;
import java.security.MessageDigest;
import java.security.NoSuchAlgorithmException;
import java.time.Instant;
import java.time.LocalDate;
import java.time.ZoneOffset;
import java.time.temporal.ChronoUnit;
import java.util.ArrayList;
import java.util.Comparator;
import java.util.HashMap;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Locale;
import java.util.Map;
import java.util.Set;
import java.util.regex.Pattern;

/**
 * 从已确认由用户本人发送的消息中提取可解释的表达风格指标。
 *
 * <p>该组件只分析表达形式，不提取姓名、学校、联系方式等事实，避免个人 Skill
 * 把历史事实错误地当成当前事实。语义层面的风格总结会在后续 Python Agent 中完成，
 * 这里保留确定性的样本治理和质量评估作为安全底座。</p>
 */
@Component
public class PersonalStyleAnalyzer {

    private static final int MAX_TRAINING_TEXT_LENGTH = 500;
    private static final int MAX_DUPLICATES_PER_TEXT = 5;
    private static final String PUNCTUATION = "，。！？!?；;：:、,.…~～";
    private static final String TERMINAL_PUNCTUATION = "。！？!?…~～";
    private static final Pattern EMOJI_PATTERN = Pattern.compile(
            "[\\x{1F300}-\\x{1FAFF}\\x{2600}-\\x{27BF}]",
            Pattern.UNICODE_CHARACTER_CLASS
    );
    private static final List<String> COMMON_ENDING_PARTICLES = List.of(
            "啊", "呀", "哈", "呢", "吧", "哦", "啦", "嘛", "呗", "诶", "哇", "咯"
    );
    private static final Set<String> TRAINABLE_ORIGINS = Set.of("USER_MANUAL", "HISTORY_CONSENTED");

    /**
     * 分析候选事件并生成风格指标、质量分和稳定性分。
     *
     * @param candidateEvents 当前用户和会话范围内的全部候选事件
     * @param minSampleCount 发布 Skill 所需的最低有效样本量
     * @param minHistorySpanDays 发布 Skill 所需的最低历史跨度
     * @return 不包含原始文本的分析结果；有效样本仅供发布器计算指纹
     */
    public PersonalStyleAnalysis analyze(
            List<StoredEvent> candidateEvents,
            int minSampleCount,
            int minHistorySpanDays
    ) {
        List<StoredEvent> ownMessages = candidateEvents.stream()
                .filter(this::isSelfAuthored)
                .toList();
        List<StoredEvent> trainableOwnMessages = ownMessages.stream()
                .filter(event -> TRAINABLE_ORIGINS.contains(normalizeOrigin(event.messageOrigin())))
                .toList();
        List<StoredEvent> samples = deduplicateAndClean(trainableOwnMessages).stream()
                .sorted(Comparator.comparing(this::eventTime))
                .toList();

        int discardedMessages = Math.max(0, trainableOwnMessages.size() - samples.size());
        double dataQuality = trainableOwnMessages.isEmpty()
                ? 0.0
                : (double) samples.size() / trainableOwnMessages.size();
        int historySpanDays = calculateHistorySpanDays(samples);
        int activeDays = calculateActiveDays(samples);
        StyleMetrics metrics = calculateMetrics(samples);
        double styleStability = calculateStyleStability(samples);
        double volumeConfidence = clamp((double) samples.size() / Math.max(1, minSampleCount));
        double temporalConfidence = calculateTemporalConfidence(
                historySpanDays,
                activeDays,
                minHistorySpanDays
        );
        double confidence = round(
                volumeConfidence * 0.30
                        + dataQuality * 0.20
                        + temporalConfidence * 0.25
                        + styleStability * 0.25
        );

        ConfidenceBreakdown breakdown = new ConfidenceBreakdown(
                round(volumeConfidence),
                round(dataQuality),
                round(temporalConfidence),
                round(styleStability)
        );
        return new PersonalStyleAnalysis(
                samples,
                ownMessages.size(),
                trainableOwnMessages.size(),
                discardedMessages,
                activeDays,
                historySpanDays,
                confidence,
                breakdown,
                metrics,
                fingerprint(samples)
        );
    }

    /**
     * 只接受 senderId 与 selfId 相同的消息，形成第二道作者归属防线。
     */
    private boolean isSelfAuthored(StoredEvent event) {
        if (event == null || event.payload() == null) {
            return false;
        }
        String actorType = event.payload().actorType();
        if ("OWNER".equalsIgnoreCase(actorType)) {
            return true;
        }
        if ("AGENT".equalsIgnoreCase(actorType)
                || "CONTACT".equalsIgnoreCase(actorType)
                || "SYSTEM".equalsIgnoreCase(actorType)) {
            return false;
        }
        return event.payload().sender() != null
                && event.payload().selfId() != null
                && !event.payload().selfId().isBlank()
                && event.payload().selfId().equals(event.payload().sender().id());
    }

    /**
     * 清理空文本、超长粘贴内容和大量重复文本，同时保留少量真实口头禅重复。
     */
    private List<StoredEvent> deduplicateAndClean(List<StoredEvent> events) {
        Map<String, Integer> duplicateCounts = new HashMap<>();
        List<StoredEvent> result = new ArrayList<>();
        for (StoredEvent event : events) {
            String text = normalizedText(event);
            if (text.length() < 2 || text.length() > MAX_TRAINING_TEXT_LENGTH) {
                continue;
            }
            int duplicateCount = duplicateCounts.getOrDefault(text, 0);
            if (duplicateCount >= MAX_DUPLICATES_PER_TEXT) {
                continue;
            }
            duplicateCounts.put(text, duplicateCount + 1);
            result.add(event);
        }
        return result;
    }

    /**
     * 计算样本覆盖的自然日跨度，避免短时间刷出的消息被误判为稳定风格。
     */
    private int calculateHistorySpanDays(List<StoredEvent> samples) {
        if (samples.isEmpty()) {
            return 0;
        }
        LocalDate first = eventTime(samples.getFirst()).atZone(ZoneOffset.UTC).toLocalDate();
        LocalDate last = eventTime(samples.getLast()).atZone(ZoneOffset.UTC).toLocalDate();
        return (int) ChronoUnit.DAYS.between(first, last) + 1;
    }

    /**
     * 统计真正出现过人工消息的日期数，用于区分长期积累和集中导入。
     */
    private int calculateActiveDays(List<StoredEvent> samples) {
        return (int) samples.stream()
                .map(this::eventTime)
                .map(instant -> instant.atZone(ZoneOffset.UTC).toLocalDate())
                .distinct()
                .count();
    }

    /**
     * 同时考虑历史跨度和活跃日期，得到时间覆盖可信度。
     */
    private double calculateTemporalConfidence(
            int historySpanDays,
            int activeDays,
            int minHistorySpanDays
    ) {
        double spanScore = clamp((double) historySpanDays / Math.max(1, minHistorySpanDays));
        int expectedActiveDays = Math.max(5, Math.min(14, minHistorySpanDays / 3));
        double activeDayScore = clamp((double) activeDays / expectedActiveDays);
        return (spanScore + activeDayScore) / 2.0;
    }

    /**
     * 提取可直接转化为提示词的长度、标点、疑问、表情和语气词指标。
     */
    private StyleMetrics calculateMetrics(List<StoredEvent> samples) {
        if (samples.isEmpty()) {
            return StyleMetrics.empty();
        }
        List<Integer> lengths = samples.stream()
                .map(this::normalizedText)
                .map(String::length)
                .sorted()
                .toList();
        long totalCharacters = samples.stream().mapToLong(event -> normalizedText(event).length()).sum();
        long punctuationCharacters = samples.stream()
                .map(this::normalizedText)
                .flatMapToInt(String::chars)
                .filter(character -> PUNCTUATION.indexOf(character) >= 0)
                .count();
        Map<String, Long> endingParticleCounts = new LinkedHashMap<>();
        for (String particle : COMMON_ENDING_PARTICLES) {
            long count = samples.stream()
                    .map(this::normalizedText)
                    .filter(text -> text.endsWith(particle))
                    .count();
            if (count > 0) {
                endingParticleCounts.put(particle, count);
            }
        }
        List<String> topEndingParticles = endingParticleCounts.entrySet().stream()
                .sorted(Map.Entry.<String, Long>comparingByValue().reversed())
                .limit(5)
                .map(Map.Entry::getKey)
                .toList();

        return new StyleMetrics(
                round(lengths.stream().mapToInt(Integer::intValue).average().orElse(0.0)),
                percentile(lengths, 0.50),
                percentile(lengths, 0.90),
                round(rate(samples, text -> text.length() <= 12)),
                round(rate(samples, this::hasTerminalPunctuation)),
                round(totalCharacters == 0 ? 0.0 : (double) punctuationCharacters / totalCharacters),
                round(rate(samples, text -> text.contains("?") || text.contains("？"))),
                round(rate(samples, text -> EMOJI_PATTERN.matcher(text).find())),
                round(rate(samples, text -> text.contains("\n") || text.contains("\r"))),
                topEndingParticles
        );
    }

    /**
     * 将样本按时间切成前后两段，比较核心指标是否稳定，防止偶然阶段性语气主导 Skill。
     */
    private double calculateStyleStability(List<StoredEvent> samples) {
        if (samples.size() < 20) {
            return 0.0;
        }
        int midpoint = samples.size() / 2;
        StyleMetrics firstHalf = calculateMetrics(samples.subList(0, midpoint));
        StyleMetrics secondHalf = calculateMetrics(samples.subList(midpoint, samples.size()));
        double lengthSimilarity = relativeSimilarity(firstHalf.medianLength(), secondHalf.medianLength());
        double terminalPunctuationSimilarity = rateSimilarity(
                firstHalf.terminalPunctuationRate(),
                secondHalf.terminalPunctuationRate()
        );
        double shortReplySimilarity = rateSimilarity(firstHalf.shortReplyRate(), secondHalf.shortReplyRate());
        double emojiSimilarity = rateSimilarity(firstHalf.emojiRate(), secondHalf.emojiRate());
        return (lengthSimilarity + terminalPunctuationSimilarity + shortReplySimilarity + emojiSimilarity) / 4.0;
    }

    /**
     * 根据谓词统计消息比例，所有输入均先经过统一文本归一化。
     */
    private double rate(List<StoredEvent> samples, java.util.function.Predicate<String> predicate) {
        if (samples.isEmpty()) {
            return 0.0;
        }
        long matched = samples.stream().map(this::normalizedText).filter(predicate).count();
        return (double) matched / samples.size();
    }

    /**
     * 判断消息末尾是否存在明显标点。
     */
    private boolean hasTerminalPunctuation(String text) {
        return !text.isBlank() && TERMINAL_PUNCTUATION.indexOf(text.charAt(text.length() - 1)) >= 0;
    }

    /**
     * 读取指定分位数，用中位数和 P90 描述用户常见长度，而不是被极端长消息拉高。
     */
    private int percentile(List<Integer> sortedValues, double percentile) {
        if (sortedValues.isEmpty()) {
            return 0;
        }
        int index = (int) Math.ceil(percentile * sortedValues.size()) - 1;
        return sortedValues.get(Math.max(0, Math.min(index, sortedValues.size() - 1)));
    }

    /**
     * 比较两个非负数值的相对接近程度。
     */
    private double relativeSimilarity(double first, double second) {
        double denominator = Math.max(1.0, Math.max(first, second));
        return clamp(1.0 - Math.abs(first - second) / denominator);
    }

    /**
     * 比较两个 0 到 1 比率的接近程度。
     */
    private double rateSimilarity(double first, double second) {
        return clamp(1.0 - Math.abs(first - second));
    }

    /**
     * 为样本集合生成稳定指纹，避免定时任务在样本未变化时重复发布版本。
     */
    private String fingerprint(List<StoredEvent> samples) {
        try {
            MessageDigest digest = MessageDigest.getInstance("SHA-256");
            samples.stream()
                    .sorted(Comparator.comparing(StoredEvent::eventId))
                    .forEach(event -> {
                        digest.update(event.eventId().getBytes(StandardCharsets.UTF_8));
                        digest.update((byte) 0);
                        digest.update(normalizedText(event).getBytes(StandardCharsets.UTF_8));
                        digest.update((byte) 0);
                    });
            return java.util.HexFormat.of().formatHex(digest.digest());
        } catch (NoSuchAlgorithmException exception) {
            throw new IllegalStateException("当前 Java 环境不支持 SHA-256", exception);
        }
    }

    /**
     * 优先使用平台消息时间；缺失或格式异常时回退到 Event Center 接收时间。
     */
    private Instant eventTime(StoredEvent event) {
        String timestamp = event.payload().timestamp();
        if (timestamp != null && !timestamp.isBlank()) {
            try {
                return Instant.parse(timestamp);
            } catch (RuntimeException ignored) {
                // 部分旧记录使用非 ISO 时间格式，回退到 receivedAt 即可。
            }
        }
        return event.receivedAt() == null ? Instant.EPOCH : event.receivedAt();
    }

    /**
     * 对文本做轻量归一化，仅用于去重和统计，不修改数据库中的原始消息。
     */
    private String normalizedText(StoredEvent event) {
        if (event.payload().text() == null) {
            return "";
        }
        return event.payload().text().trim().replaceAll("\\s+", " ");
    }

    /**
     * 统一消息来源的大小写，兼容旧数据。
     */
    private String normalizeOrigin(String origin) {
        return origin == null ? "" : origin.trim().toUpperCase(Locale.ROOT);
    }

    /**
     * 将数值限制在 0 到 1 之间。
     */
    private double clamp(double value) {
        return Math.max(0.0, Math.min(1.0, value));
    }

    /**
     * 统一保留三位小数，避免描述文件频繁出现无意义浮点差异。
     */
    private double round(double value) {
        return Math.round(value * 1000.0) / 1000.0;
    }

    /**
     * 个人风格分析结果。samples 仅在发布阶段计算版本，不会写入 Skill 描述文件。
     */
    public record PersonalStyleAnalysis(
            List<StoredEvent> samples,
            int observedOwnMessages,
            int trainableOwnMessages,
            int discardedMessages,
            int activeDays,
            int historySpanDays,
            double confidence,
            ConfidenceBreakdown confidenceBreakdown,
            StyleMetrics metrics,
            String fingerprint
    ) {
    }

    /**
     * 可信度分项，便于前端解释 Skill 为什么尚未成熟。
     */
    public record ConfidenceBreakdown(
            double volume,
            double dataQuality,
            double temporalCoverage,
            double styleStability
    ) {
    }

    /**
     * 只描述表达形式的统计指标，不包含任何历史事实或原始消息。
     */
    public record StyleMetrics(
            double averageLength,
            int medianLength,
            int p90Length,
            double shortReplyRate,
            double terminalPunctuationRate,
            double punctuationDensity,
            double questionRate,
            double emojiRate,
            double multilineRate,
            List<String> commonEndingParticles
    ) {
        /**
         * 返回无样本时的安全默认值。
         */
        public static StyleMetrics empty() {
            return new StyleMetrics(0, 0, 0, 0, 0, 0, 0, 0, 0, List.of());
        }
    }
}
