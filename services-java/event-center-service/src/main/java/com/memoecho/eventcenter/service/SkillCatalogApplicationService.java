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
        // 这个函数的作用是把 github:// 风格的 skill 引用下载到本地缓存目录，并写成 runtime 可直接解析的 skill.json。
        GithubSkillReference reference = parseGithubReference(request.reference(), request.gitRef());
        String rawDescriptor = downloader.downloadSkillDescriptor(reference);
        JsonNode descriptorNode = parseJson(rawDescriptor);
        ObjectNode normalizedDescriptor = normalizeDescriptor(descriptorNode, reference);

        Path installDirectory = resolveInstalledRoot().resolve(reference.installSubdirectory()).normalize();
        try {
            Files.createDirectories(installDirectory);
            Files.writeString(
                    installDirectory.resolve("skill.json"),
                    objectMapper.writerWithDefaultPrettyPrinter().writeValueAsString(normalizedDescriptor)
            );
            Files.writeString(
                    installDirectory.resolve("origin.json"),
                    objectMapper.writerWithDefaultPrettyPrinter().writeValueAsString(buildOriginMetadata(reference))
            );
        } catch (IOException ex) {
            throw new ResponseStatusException(HttpStatus.INTERNAL_SERVER_ERROR, "写入已安装 skill 缓存失败", ex);
        }

        SkillDescriptorResponse descriptorResponse = toSkillDescriptorResponse(
                normalizedDescriptor,
                request.reference().trim(),
                "github",
                true,
                installDirectory.toString()
        );
        return new SkillInstallResponse(
                "installed",
                request.reference().trim(),
                request.reference().trim(),
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
            String reference = detectReference(node, descriptorPath, root, sourceType);
            return toSkillDescriptorResponse(node, reference, sourceType, installed, descriptorPath.getParent().toString());
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
        SkillDescriptorResponse descriptor = readDescriptor(
                descriptorPath,
                rawReference.startsWith("github://") ? resolveInstalledRoot() : resolveBuiltinRoot(),
                rawReference.startsWith("github://") ? "github" : "builtin",
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

        Path base = resolveBuiltinRoot().resolve(normalized).normalize();
        if (base.toString().endsWith(".json")) {
            return base;
        }
        return base.resolve("skill.json");
    }

    private GithubSkillReference parseGithubReference(String rawReference, String explicitGitRef) {
        // 这个函数的作用是解析 github://owner/repo/path 或 github://owner/repo@ref/path 这两种常用写法，统一生成下载和安装所需的结构化引用。
        String reference = normalizeText(rawReference);
        if (!reference.startsWith("github://")) {
            throw new ResponseStatusException(HttpStatus.BAD_REQUEST, "只支持 github:// 开头的 skill 引用");
        }
        String withoutScheme = reference.substring("github://".length());
        String[] segments = withoutScheme.split("/", 4);
        if (segments.length < 3) {
            throw new ResponseStatusException(HttpStatus.BAD_REQUEST, "GitHub skill 引用格式不正确，应为 github://owner/repo/path");
        }

        String owner = segments[0].trim();
        String repositoryWithRef = segments[1].trim();
        String path = segments.length == 3 ? segments[2].trim() : (segments[2] + "/" + segments[3]).trim();

        String repository = repositoryWithRef;
        String gitRef = normalizeText(explicitGitRef);
        int refSeparatorIndex = repositoryWithRef.indexOf('@');
        if (refSeparatorIndex >= 0) {
            repository = repositoryWithRef.substring(0, refSeparatorIndex).trim();
            if (gitRef.isBlank()) {
                gitRef = repositoryWithRef.substring(refSeparatorIndex + 1).trim();
            }
        }
        if (owner.isBlank() || repository.isBlank() || path.isBlank()) {
            throw new ResponseStatusException(HttpStatus.BAD_REQUEST, "GitHub skill 引用缺少 owner、repo 或 path");
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
        descriptor.put("rawReference", reference.originalReference());
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

    private ObjectNode buildOriginMetadata(GithubSkillReference reference) {
        // 这个函数的作用是把安装来源记录到 origin.json，便于后续前端展示、排障和升级已安装 skill。
        ObjectNode node = objectMapper.createObjectNode();
        node.put("reference", reference.originalReference());
        node.put("owner", reference.owner());
        node.put("repository", reference.repository());
        node.put("gitRef", reference.gitRef());
        node.put("path", reference.path());
        node.put("rawDescriptorUrl", reference.rawDescriptorUrl());
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
        return Path.of(properties.getBuiltinRoot()).toAbsolutePath().normalize();
    }

    private Path resolveInstalledRoot() {
        // 这个函数的作用是统一解析已安装 skill 缓存目录，并保证目录不存在时也能被后续安装流程创建。
        return Path.of(properties.getInstalledRoot()).toAbsolutePath().normalize();
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
