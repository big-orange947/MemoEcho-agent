package com.memoecho.eventcenter.service;

import com.fasterxml.jackson.core.JsonProcessingException;
import com.fasterxml.jackson.databind.JsonNode;
import com.fasterxml.jackson.databind.ObjectMapper;
import com.fasterxml.jackson.databind.node.ObjectNode;
import com.memoecho.eventcenter.config.SkillStoreProperties;
import com.memoecho.eventcenter.dto.ConversationProfileConfigurationResponse;
import com.memoecho.eventcenter.dto.GithubSkillInstallRequest;
import com.memoecho.eventcenter.dto.SkillDescriptorResponse;
import com.memoecho.eventcenter.dto.SkillInstallResponse;
import com.memoecho.eventcenter.dto.SkillModelHintsResponse;
import com.memoecho.eventcenter.dto.SkillPromptFragmentsResponse;
import com.memoecho.eventcenter.dto.SkillResolvePreviewRequest;
import com.memoecho.eventcenter.dto.SkillResolvePreviewResponse;
import com.memoecho.eventcenter.dto.SkillToolPolicyResponse;
import com.memoecho.eventcenter.model.GithubSkillReference;
import org.springframework.http.HttpStatus;
import org.springframework.stereotype.Service;
import org.springframework.web.server.ResponseStatusException;

import java.io.IOException;
import java.net.URI;
import java.nio.file.Files;
import java.nio.file.Path;
import java.time.Instant;
import java.util.ArrayList;
import java.util.Comparator;
import java.util.LinkedHashSet;
import java.util.List;
import java.util.Locale;
import java.util.Set;
import java.util.stream.Stream;

@Service
public class SkillCatalogApplicationService {

    private static final int MAX_SKILL_MARKDOWN_CHARS = 80_000;

    private static final List<String> SUPPORTED_PLATFORMS = List.of("qq", "wechat", "telegram", "discord");
    private static final List<String> SUPPORTED_SCENES = List.of("life", "work");
    private static final List<String> CHAT_TYPES = List.of("private", "group");
    private static final List<String> TRIGGER_MODES = List.of(
            "ALWAYS",
            "AT_SELF_ONLY",
            "KEYWORD_ONLY",
            "AT_SELF_OR_KEYWORD",
            "ADMIN_OR_AT_SELF",
            "MANUAL_ONLY"
    );
    private static final List<String> REPLY_MODES = List.of("AUTO_REPLY", "DRAFT_ONLY", "SILENT");
    private static final List<String> PERSONA_MODES = List.of("NONE", "PROMPT", "SKILL");
    private static final List<String> SUPPORTED_ROUTES = List.of(
            "message_dispatch",
            "file_analysis",
            "schedule_extract",
            "task_plan",
            "social_reply",
            "group_ops",
            "chat_summary"
    );
    private static final List<String> AVAILABLE_TOOLS = List.of(
            "create_schedule",
            "create_task",
            "extract_file_text",
            "get_recent_messages",
            "list_tasks",
            "send_qq_message"
    );

    private final SkillStoreProperties properties;
    private final GithubSkillDescriptorDownloader downloader;
    private final ObjectMapper objectMapper;

    public SkillCatalogApplicationService(
            SkillStoreProperties properties,
            GithubSkillDescriptorDownloader downloader,
            ObjectMapper objectMapper
    ) {
        // 这个构造函数的作用是注入 skill 存储位置、GitHub 下载能力和 JSON 解析器，统一承接 skill 列表、安装与预览解析能力。
        this.properties = properties;
        this.downloader = downloader;
        this.objectMapper = objectMapper;
    }

    public List<SkillDescriptorResponse> listSkills() {
        // 这个函数的作用是扫描内置 skill 与已安装 skill 目录，返回前端可直接展示和选择的 skill 清单。
        List<SkillDescriptorResponse> descriptors = new ArrayList<>();
        descriptors.addAll(scanRoot(resolveBuiltinRoot(), "builtin", true));
        descriptors.addAll(scanRoot(resolveInstalledRoot(), "github", true));
        return descriptors.stream()
                .sorted(Comparator.comparing(SkillDescriptorResponse::sourceType).thenComparing(SkillDescriptorResponse::id))
                .toList();
    }

    public ConversationProfileConfigurationResponse buildConversationProfileConfiguration() {
        // 这个函数的作用是把会话设定页需要的全部枚举、route、tool 和可用 skill 一次性打包给前端，避免 UI 再硬编码一套常量。
        return new ConversationProfileConfigurationResponse(
                SUPPORTED_PLATFORMS,
                SUPPORTED_SCENES,
                CHAT_TYPES,
                TRIGGER_MODES,
                REPLY_MODES,
                PERSONA_MODES,
                SUPPORTED_ROUTES,
                AVAILABLE_TOOLS,
                listSkills()
        );
    }

    public SkillInstallResponse installGithubSkill(GithubSkillInstallRequest request) {
        // 这个函数的作用是优先安装 Memo Echo skill.json；不存在时把通用 SKILL.md 转换为 Runtime 可解析的本地描述符。
        GithubSkillReference reference = parseGithubReference(request.reference(), request.gitRef());
        ObjectNode normalizedDescriptor;
        String sourceFormat = "skill.json";
        try {
            String rawDescriptor = downloader.downloadSkillDescriptor(reference);
            JsonNode descriptorNode = parseJson(rawDescriptor);
            normalizedDescriptor = normalizeDescriptor(descriptorNode, reference);
        } catch (ResponseStatusException descriptorError) {
            String skillMarkdown = downloader.downloadSkillMarkdown(reference);
            if (skillMarkdown == null || skillMarkdown.isBlank()) {
                throw new ResponseStatusException(
                        HttpStatus.BAD_REQUEST,
                        "仓库中未找到可解析的 skill.json 或 SKILL.md",
                        descriptorError
                );
            }
            normalizedDescriptor = normalizeSkillMarkdown(skillMarkdown, reference);
            sourceFormat = "SKILL.md";
        }

        Path installDirectory = resolveInstalledRoot().resolve(reference.installSubdirectory()).normalize();
        try {
            Files.createDirectories(installDirectory);
            Files.writeString(
                    installDirectory.resolve("skill.json"),
                    objectMapper.writerWithDefaultPrettyPrinter().writeValueAsString(normalizedDescriptor)
            );
            Files.writeString(
                    installDirectory.resolve("origin.json"),
                    objectMapper.writerWithDefaultPrettyPrinter().writeValueAsString(buildOriginMetadata(reference, sourceFormat))
            );
        } catch (IOException ex) {
            throw new ResponseStatusException(HttpStatus.INTERNAL_SERVER_ERROR, "写入已安装 skill 缓存失败", ex);
        }

        SkillDescriptorResponse descriptorResponse = toSkillDescriptorResponse(
                normalizedDescriptor,
                reference.runtimeReference(),
                "github",
                true,
                installDirectory.toString()
        );
        return new SkillInstallResponse(
                "installed",
                reference.runtimeReference(),
                reference.runtimeReference(),
                "github",
                installDirectory.toString(),
                descriptorResponse
        );
    }

    public SkillResolvePreviewResponse previewResolve(SkillResolvePreviewRequest request) {
        // 这个函数的作用是让前端在真正保存会话设定前，先预览某组 skill 引用在当前 route 下哪些能生效、哪些会失效。
        List<SkillDescriptorResponse> resolvedSkills = new ArrayList<>();
        List<String> unresolvedSkillReferences = new ArrayList<>();
        for (String rawReference : normalizeList(request.skillReferences())) {
            SkillDescriptorResponse descriptor = resolveSingleReference(rawReference, normalizeText(request.route()));
            if (descriptor == null) {
                unresolvedSkillReferences.add(rawReference);
                continue;
            }
            resolvedSkills.add(descriptor);
        }
        return new SkillResolvePreviewResponse(resolvedSkills, unresolvedSkillReferences);
    }

    private List<SkillDescriptorResponse> scanRoot(Path root, String sourceType, boolean installed) {
        // 这个函数的作用是递归扫描某个 skill 根目录下的全部 skill.json，并把它们转换成统一返回结构。
        if (!Files.exists(root)) {
            return List.of();
        }
        try (Stream<Path> stream = Files.walk(root)) {
            return stream
                    .filter(path -> Files.isRegularFile(path) && path.getFileName().toString().equalsIgnoreCase("skill.json"))
                    .map(path -> readDescriptor(path, root, sourceType, installed))
                    .toList();
        } catch (IOException ex) {
            throw new ResponseStatusException(HttpStatus.INTERNAL_SERVER_ERROR, "扫描 skill 目录失败", ex);
        }
    }

    private SkillDescriptorResponse readDescriptor(Path descriptorPath, Path root, String sourceType, boolean installed) {
        // 这个函数的作用是读取单个 skill.json，并补齐前端展示需要的 reference、来源和目录位置信息。
        try {
            JsonNode node = objectMapper.readTree(Files.readString(descriptorPath));
            String effectiveSourceType = readText(node, "source").isBlank() ? sourceType : readText(node, "source");
            String reference = detectReference(node, descriptorPath, root, effectiveSourceType);
            return toSkillDescriptorResponse(node, reference, effectiveSourceType, installed, descriptorPath.getParent().toString());
        } catch (IOException ex) {
            throw new ResponseStatusException(HttpStatus.INTERNAL_SERVER_ERROR, "读取 skill 描述文件失败", ex);
        }
    }

    private String detectReference(JsonNode node, Path descriptorPath, Path root, String sourceType) {
        // 这个函数的作用是为前端恢复“应该写回会话设定里的 reference 字符串”，避免 UI 还要自己拼路径。
        String rawReference = readText(node, "rawReference");
        if (!rawReference.isBlank()) {
            return rawReference;
        }
        Path relative = root.relativize(descriptorPath.getParent());
        String normalizedPath = relative.toString().replace('\\', '/');
        if ("builtin".equals(sourceType)) {
            return "skills/" + normalizedPath;
        }
        return normalizedPath;
    }

    private SkillDescriptorResponse resolveSingleReference(String rawReference, String route) {
        // 这个函数的作用是按 reference 解析单个 skill，并在 route 不匹配时把它视为当前上下文下不可用。
        Path descriptorPath = locateDescriptor(rawReference);
        if (descriptorPath == null || !Files.exists(descriptorPath)) {
            return null;
        }
        Path installedRoot = resolveInstalledRoot();
        boolean installedSkill = descriptorPath.startsWith(installedRoot);
        SkillDescriptorResponse descriptor = readDescriptor(
                descriptorPath,
                installedSkill ? installedRoot : resolveBuiltinRoot(),
                rawReference.startsWith("github://") ? "github" : (installedSkill ? "installed" : "builtin"),
                true
        );
        if (!route.isBlank() && !descriptor.applicableRoutes().isEmpty() && descriptor.applicableRoutes().stream().noneMatch(route::equalsIgnoreCase)) {
            return null;
        }
        return descriptor;
    }

    private Path locateDescriptor(String rawReference) {
        // 这个函数的作用是把前端提交的 reference 定位到实际的 skill.json 路径，统一兼容本地目录、本地文件和已安装 GitHub skill。
        String reference = normalizeText(rawReference);
        if (reference.isBlank()) {
            return null;
        }
        if (reference.startsWith("github://")) {
            GithubSkillReference githubReference = parseGithubReference(reference, null);
            return resolveInstalledRoot().resolve(githubReference.installSubdirectory()).resolve("skill.json").normalize();
        }

        String normalized = reference.replace('\\', '/');
        if (normalized.startsWith("local://")) {
            normalized = normalized.substring("local://".length());
        }
        if (normalized.startsWith("skills/")) {
            normalized = normalized.substring("skills/".length());
        }

        Path builtinCandidate = descriptorCandidate(resolveBuiltinRoot(), normalized);
        if (Files.exists(builtinCandidate)) {
            return builtinCandidate;
        }
        Path installedCandidate = descriptorCandidate(resolveInstalledRoot(), normalized);
        return Files.exists(installedCandidate) ? installedCandidate : builtinCandidate;
    }

    /**
     * 把相对 Skill 引用转换成描述文件候选路径，统一兼容目录引用和直接 JSON 文件引用。
     */
    private Path descriptorCandidate(Path root, String normalizedReference) {
        Path base = root.resolve(normalizedReference).normalize();
        return base.toString().endsWith(".json") ? base : base.resolve("skill.json");
    }

    private GithubSkillReference parseGithubReference(String rawReference, String explicitGitRef) {
        // 这个函数的作用是统一解析 github:// 引用和浏览器复制的 GitHub 仓库/tree/blob URL。
        String reference = normalizeText(rawReference);
        if (reference.startsWith("https://github.com/") || reference.startsWith("http://github.com/")) {
            return parseGithubWebUrl(reference, explicitGitRef);
        }
        if (!reference.startsWith("github://")) {
            throw new ResponseStatusException(HttpStatus.BAD_REQUEST, "请输入 GitHub 仓库 URL 或 github://owner/repo/path 引用");
        }
        String withoutScheme = reference.substring("github://".length());
        String[] segments = withoutScheme.split("/", 4);
        if (segments.length < 2) {
            throw new ResponseStatusException(HttpStatus.BAD_REQUEST, "GitHub skill 引用格式不正确，应为 github://owner/repo[/path]");
        }

        String owner = segments[0].trim();
        String repositoryWithRef = segments[1].trim();
        String path = segments.length < 3 ? "" : (segments.length == 3 ? segments[2].trim() : (segments[2] + "/" + segments[3]).trim());
        path = stripSkillFileName(path);

        String repository = repositoryWithRef;
        String gitRef = normalizeText(explicitGitRef);
        int refSeparatorIndex = repositoryWithRef.indexOf('@');
        if (refSeparatorIndex >= 0) {
            repository = repositoryWithRef.substring(0, refSeparatorIndex).trim();
            if (gitRef.isBlank()) {
                gitRef = repositoryWithRef.substring(refSeparatorIndex + 1).trim();
            }
        }
        if (owner.isBlank() || repository.isBlank()) {
            throw new ResponseStatusException(HttpStatus.BAD_REQUEST, "GitHub skill 引用缺少 owner 或 repo");
        }
        if (gitRef.isBlank()) {
            gitRef = normalizeText(properties.getGithubDefaultRef());
        }

        return new GithubSkillReference(
                reference,
                owner,
                repository,
                gitRef,
                path.replace('\\', '/')
        );
    }

    private GithubSkillReference parseGithubWebUrl(String rawReference, String explicitGitRef) {
        // 这个函数的作用是把 https://github.com/owner/repo、tree 和 blob URL 转换成统一的安装引用。
        try {
            URI uri = URI.create(rawReference);
            String[] segments = uri.getPath().replaceFirst("^/", "").split("/");
            if (segments.length < 2) {
                throw new ResponseStatusException(HttpStatus.BAD_REQUEST, "GitHub 仓库 URL 缺少 owner 或 repo");
            }
            String owner = segments[0].trim();
            String repository = segments[1].replaceFirst("\\.git$", "").trim();
            String gitRef = normalizeText(explicitGitRef);
            String path = "";
            if (segments.length >= 4 && ("tree".equals(segments[2]) || "blob".equals(segments[2]))) {
                if (gitRef.isBlank()) {
                    gitRef = segments[3].trim();
                }
                if (segments.length > 4) {
                    path = String.join("/", java.util.Arrays.copyOfRange(segments, 4, segments.length));
                }
            }
            if (gitRef.isBlank()) {
                gitRef = normalizeText(properties.getGithubDefaultRef());
            }
            return new GithubSkillReference(rawReference, owner, repository, gitRef, stripSkillFileName(path));
        } catch (IllegalArgumentException ex) {
            throw new ResponseStatusException(HttpStatus.BAD_REQUEST, "GitHub 仓库 URL 格式不正确", ex);
        }
    }

    private String stripSkillFileName(String path) {
        // 这个函数把指向 skill.json 或 SKILL.md 文件本身的 URL 归一化为 Skill 所在目录。
        String normalized = normalizeText(path).replace('\\', '/').replaceAll("^/+|/+$", "");
        if (normalized.equalsIgnoreCase("skill.json") || normalized.equalsIgnoreCase("SKILL.md")) {
            return "";
        }
        if (normalized.toLowerCase(Locale.ROOT).endsWith("/skill.json")) {
            return normalized.substring(0, normalized.length() - "/skill.json".length());
        }
        if (normalized.toLowerCase(Locale.ROOT).endsWith("/skill.md")) {
            return normalized.substring(0, normalized.length() - "/skill.md".length());
        }
        return normalized;
    }

    private ObjectNode normalizeSkillMarkdown(String markdown, GithubSkillReference reference) {
        // 这个函数的作用是把 Agent Skills 的 YAML frontmatter + Markdown 正文转换成只读 Prompt Skill。
        String normalized = markdown == null ? "" : markdown.replace("\r\n", "\n").trim();
        if (normalized.isBlank()) {
            throw new ResponseStatusException(HttpStatus.BAD_REQUEST, "SKILL.md 内容为空");
        }
        if (normalized.length() > MAX_SKILL_MARKDOWN_CHARS) {
            throw new ResponseStatusException(HttpStatus.BAD_REQUEST, "SKILL.md 过大，最大允许 80000 个字符");
        }

        String name = reference.repository();
        String description = "从通用 Agent Skills SKILL.md 导入";
        String body = normalized;
        if (normalized.startsWith("---\n")) {
            int closing = normalized.indexOf("\n---\n", 4);
            if (closing > 0) {
                String frontmatter = normalized.substring(4, closing);
                String[] frontmatterLines = frontmatter.split("\n");
                for (int index = 0; index < frontmatterLines.length; index++) {
                    String line = frontmatterLines[index];
                    int separator = line.indexOf(':');
                    if (separator <= 0) {
                        continue;
                    }
                    String key = line.substring(0, separator).trim();
                    String value = stripYamlScalar(line.substring(separator + 1));
                    if ("description".equalsIgnoreCase(key) && ("|".equals(value) || ">".equals(value))) {
                        StringBuilder multilineDescription = new StringBuilder();
                        while (index + 1 < frontmatterLines.length) {
                            String nextLine = frontmatterLines[index + 1];
                            if (!nextLine.isBlank() && !Character.isWhitespace(nextLine.charAt(0))) {
                                break;
                            }
                            index++;
                            String fragment = nextLine.trim();
                            if (!fragment.isBlank()) {
                                if (!multilineDescription.isEmpty()) {
                                    multilineDescription.append(' ');
                                }
                                multilineDescription.append(fragment);
                            }
                        }
                        value = multilineDescription.toString();
                    }
                    if ("name".equalsIgnoreCase(key) && !value.isBlank()) {
                        name = value;
                    } else if ("description".equalsIgnoreCase(key) && !value.isBlank()) {
                        description = value;
                    }
                }
                body = normalized.substring(closing + 5).trim();
            }
        }
        if (body.isBlank()) {
            throw new ResponseStatusException(HttpStatus.BAD_REQUEST, "SKILL.md 没有可执行的正文内容");
        }

        ObjectNode descriptor = objectMapper.createObjectNode();
        descriptor.put("id", buildFallbackId(reference));
        descriptor.put("name", name);
        descriptor.put("description", description);
        descriptor.put("version", "1.0.0");
        descriptor.put("type", "prompt");
        descriptor.putArray("applicableRoutes").add("social_reply");
        descriptor.putObject("promptFragments").put("system", body);
        descriptor.putObject("toolPolicy").putArray("allow");
        descriptor.putObject("modelHints");
        descriptor.put("importFormat", "agent-skills-markdown");
        return normalizeDescriptor(descriptor, reference);
    }

    private String stripYamlScalar(String value) {
        // 这个函数只读取安全的一行 YAML 标量，不尝试执行标签、引用或复杂 YAML 结构。
        String normalized = normalizeText(value);
        if (normalized.length() >= 2 && ((normalized.startsWith("\"") && normalized.endsWith("\"")) || (normalized.startsWith("'") && normalized.endsWith("'")))) {
            return normalized.substring(1, normalized.length() - 1).trim();
        }
        return normalized;
    }

    private ObjectNode normalizeDescriptor(JsonNode sourceNode, GithubSkillReference reference) {
        // 这个函数的作用是把远程下载得到的描述文件补齐 runtime 所需的最小字段，保证 Python 侧可以无条件解析。
        ObjectNode descriptor = sourceNode != null && sourceNode.isObject()
                ? ((ObjectNode) sourceNode).deepCopy()
                : objectMapper.createObjectNode();

        if (readText(descriptor, "id").isBlank()) {
            descriptor.put("id", buildFallbackId(reference));
        }
        if (readText(descriptor, "name").isBlank()) {
            descriptor.put("name", buildFallbackId(reference));
        }
        if (readText(descriptor, "version").isBlank()) {
            descriptor.put("version", "1.0.0");
        }
        if (readText(descriptor, "type").isBlank()) {
            descriptor.put("type", "prompt");
        }
        if (!descriptor.has("description")) {
            descriptor.put("description", "");
        }
        descriptor.put("source", "github");
        descriptor.put("rawReference", reference.runtimeReference());
        if (!descriptor.has("applicableRoutes")) {
            descriptor.putArray("applicableRoutes");
        }
        if (!descriptor.has("promptFragments") || !descriptor.get("promptFragments").isObject()) {
            ObjectNode promptFragments = objectMapper.createObjectNode();
            promptFragments.put("system", "");
            descriptor.set("promptFragments", promptFragments);
        } else if (readText(descriptor.get("promptFragments"), "system").isBlank()) {
            ((ObjectNode) descriptor.get("promptFragments")).put("system", "");
        }
        if (!descriptor.has("toolPolicy") || !descriptor.get("toolPolicy").isObject()) {
            ObjectNode toolPolicy = objectMapper.createObjectNode();
            toolPolicy.putArray("allow");
            descriptor.set("toolPolicy", toolPolicy);
        } else if (!descriptor.get("toolPolicy").has("allow")) {
            ((ObjectNode) descriptor.get("toolPolicy")).putArray("allow");
        }
        if (!descriptor.has("modelHints") || !descriptor.get("modelHints").isObject()) {
            descriptor.set("modelHints", objectMapper.createObjectNode());
        }
        return descriptor;
    }

    private ObjectNode buildOriginMetadata(GithubSkillReference reference, String sourceFormat) {
        // 这个函数的作用是把安装来源记录到 origin.json，便于后续前端展示、排障和升级已安装 skill。
        ObjectNode node = objectMapper.createObjectNode();
        node.put("reference", reference.originalReference());
        node.put("runtimeReference", reference.runtimeReference());
        node.put("owner", reference.owner());
        node.put("repository", reference.repository());
        node.put("gitRef", reference.gitRef());
        node.put("path", reference.path());
        node.put("rawDescriptorUrl", reference.rawDescriptorUrl());
        node.put("sourceFormat", sourceFormat);
        node.put("installedAt", Instant.now().toString());
        return node;
    }

    private SkillDescriptorResponse toSkillDescriptorResponse(
            JsonNode node,
            String reference,
            String sourceType,
            boolean installed,
            String location
    ) {
        // 这个函数的作用是把磁盘上的 skill 描述信息转换成前端和配置接口都可复用的统一结构。
        return new SkillDescriptorResponse(
                readText(node, "id"),
                readText(node, "name"),
                defaultText(readText(node, "version"), "1.0.0"),
                defaultText(readText(node, "type"), "prompt"),
                readText(node, "description"),
                sourceType,
                reference,
                readStringList(node.path("applicableRoutes")),
                new SkillPromptFragmentsResponse(readText(node.path("promptFragments"), "system")),
                new SkillToolPolicyResponse(readStringList(node.path("toolPolicy").path("allow"))),
                new SkillModelHintsResponse(
                        node.path("modelHints").path("temperature").isNumber() ? node.path("modelHints").path("temperature").doubleValue() : null,
                        node.path("modelHints").path("maxTokens").isInt() ? node.path("modelHints").path("maxTokens").intValue() : null
                ),
                installed,
                location
        );
    }

    private JsonNode parseJson(String content) {
        // 这个函数的作用是把下载到的 skill 描述文本解析为 JSON 节点，并在格式不正确时尽早给出明确错误。
        try {
            return objectMapper.readTree(content);
        } catch (JsonProcessingException ex) {
            throw new ResponseStatusException(HttpStatus.BAD_REQUEST, "GitHub skill 描述文件不是合法 JSON", ex);
        }
    }

    private Path resolveBuiltinRoot() {
        // 这个函数的作用是统一解析内置 skill 根目录，避免后续多个方法重复处理相对路径。
        return resolveSkillRoot(properties.getBuiltinRoot(), "skills");
    }

    private Path resolveInstalledRoot() {
        // 这个函数的作用是统一解析已安装 skill 缓存目录，并保证目录不存在时也能被后续安装流程创建。
        return resolveSkillRoot(properties.getInstalledRoot(), "skills-installed");
    }

    private Path resolveSkillRoot(String configuredRoot, String folderName) {
        // 绝对路径完全尊重用户配置；默认相对路径则优先锚定到实际仓库根目录。
        Path configuredPath = Path.of(configuredRoot);
        if (configuredPath.isAbsolute()) {
            return configuredPath.normalize();
        }
        Path repositoryRoot = findRepositoryRoot(Path.of("").toAbsolutePath().normalize());
        String normalizedConfig = configuredRoot.replace('\\', '/');
        if (repositoryRoot != null && normalizedConfig.contains("agent-runtime-python")) {
            return repositoryRoot.resolve("agent-runtime-python").resolve(folderName).normalize();
        }
        return configuredPath.toAbsolutePath().normalize();
    }

    private Path findRepositoryRoot(Path startDirectory) {
        // 从当前工作目录逐级向上查找同时包含 Java 服务与 Python Runtime 的仓库根目录。
        Path current = startDirectory;
        while (current != null) {
            if (Files.isDirectory(current.resolve("services-java"))
                    && Files.isDirectory(current.resolve("agent-runtime-python"))) {
                return current;
            }
            current = current.getParent();
        }
        return null;
    }

    private List<String> normalizeList(List<String> values) {
        // 这个函数的作用是清理前端传入的字符串列表，去除空值和重复项，减少后续解析分支。
        if (values == null) {
            return List.of();
        }
        Set<String> ordered = new LinkedHashSet<>();
        for (String value : values) {
            String normalized = normalizeText(value);
            if (!normalized.isBlank()) {
                ordered.add(normalized);
            }
        }
        return List.copyOf(ordered);
    }

    private List<String> readStringList(JsonNode node) {
        // 这个函数的作用是从 JSON 数组里读取字符串列表，并自动跳过空值，避免描述文件字段不规范时污染前端结果。
        if (node == null || !node.isArray()) {
            return List.of();
        }
        List<String> values = new ArrayList<>();
        node.forEach(item -> {
            String text = normalizeText(item.asText(""));
            if (!text.isBlank()) {
                values.add(text);
            }
        });
        return values;
    }

    private String buildFallbackId(GithubSkillReference reference) {
        // 这个函数的作用是为缺失 id 的远程 skill 生成稳定回退标识，避免运行时因为字段缺失而无法加载。
        String normalizedPath = reference.path()
                .replace('\\', '.')
                .replace('/', '.')
                .replace(".json", "")
                .replace("-", "_");
        return "github." + reference.owner() + "." + reference.repository() + "." + normalizedPath;
    }

    private String readText(JsonNode node, String fieldName) {
        // 这个函数的作用是安全读取 JSON 对象中的文本字段，并在字段不存在时返回空串而不是抛异常。
        if (node == null || node.isMissingNode() || node.isNull()) {
            return "";
        }
        JsonNode child = node.path(fieldName);
        return child.isMissingNode() || child.isNull() ? "" : child.asText("").trim();
    }

    private String normalizeText(String value) {
        // 这个函数的作用是统一清理单个字符串输入，避免前后空格和 null 带来的解析歧义。
        return value == null ? "" : value.trim();
    }

    private String defaultText(String value, String fallback) {
        // 这个函数的作用是在描述文件字段为空时补默认值，保持前端返回结构稳定。
        return value == null || value.isBlank() ? fallback : value;
    }
}
