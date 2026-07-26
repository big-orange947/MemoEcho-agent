package com.memoecho.connector.qqnapcat.service;

import com.memoecho.connector.qqnapcat.config.NapcatWebUiProperties;
import org.springframework.stereotype.Component;

import java.io.IOException;
import java.nio.charset.StandardCharsets;
import java.nio.file.FileSystems;
import java.nio.file.Files;
import java.nio.file.Path;
import java.time.Duration;
import java.util.ArrayList;
import java.util.LinkedHashSet;
import java.util.List;
import java.util.Set;
import java.util.concurrent.TimeUnit;
import java.util.regex.Matcher;
import java.util.regex.Pattern;

/**
 * 在本机发现 NapCat WebUI Token。
 * 只执行固定的 docker 只读命令，不接受任何来自 HTTP 请求的命令参数。
 */
@Component
public class NapcatWebUiTokenResolver {

    private static final Pattern JSON_TOKEN = Pattern.compile("[\\\"']token[\\\"']\\s*:\\s*[\\\"']([^\\\"']+)");
    private static final Pattern URL_TOKEN = Pattern.compile("(?:[?&]token=|Login Token(?: is|:)?\\s+)([A-Za-z0-9._~-]+)", Pattern.CASE_INSENSITIVE);
    private static final Duration COMMAND_TIMEOUT = Duration.ofSeconds(3);

    private final NapcatWebUiProperties properties;

    public NapcatWebUiTokenResolver(NapcatWebUiProperties properties) {
        this.properties = properties;
    }

    /**
     * 返回按可信度排序的 Token 候选：显式配置、Docker 配置文件、Docker 日志、Docker 默认值。
     */
    public List<String> resolveCandidates() {
        Set<String> candidates = new LinkedHashSet<>();
        addIfPresent(candidates, properties.getToken());
        for (Path configPath : nativeConfigPaths()) {
            addIfPresent(candidates, extractToken(readFile(configPath)));
        }
        for (String container : containerNames()) {
            addIfPresent(candidates, extractToken(runDocker("exec", container, "cat", "/app/napcat/config/webui.json")));
            addIfPresent(candidates, extractToken(runDocker("logs", "--tail", "200", container)));
        }
        // NapCat-Docker 的默认登录 Token 是 napcat；放在最后，避免覆盖用户自己的随机 Token。
        candidates.add("napcat");
        return new ArrayList<>(candidates);
    }

    /**
     * 判断当前 NapCat 是否来自正在运行的 Docker 容器，用于选择容器访问宿主机的回调地址。
     */
    public boolean isDockerDeployment() {
        for (String container : containerNames()) {
            String running = runDocker("inspect", "--format", "{{.State.Running}}", container).trim();
            if ("true".equalsIgnoreCase(running)) {
                return true;
            }
        }
        return false;
    }

    /**
     * 汇总用户显式配置和常见原生安装目录；这里只检查固定文件，不递归扫描用户磁盘。
     */
    private List<Path> nativeConfigPaths() {
        Set<Path> paths = new LinkedHashSet<>();
        addManagedRuntimeConfig(paths);
        String configuredPaths = properties.getNativeConfigPaths();
        if (configuredPaths != null && !configuredPaths.isBlank()) {
            java.util.Arrays.stream(configuredPaths.split("[;,]"))
                    .map(String::trim)
                    .filter(value -> !value.isBlank())
                    .map(Path::of)
                    .map(this::normalizeConfigPath)
                    .forEach(paths::add);
        }

        String userHome = System.getProperty("user.home", "");
        if (!userHome.isBlank()) {
            Path home = Path.of(userHome);
            paths.add(home.resolve("napcat/config/webui.json"));
            paths.add(home.resolve("NapCat/config/webui.json"));
            paths.add(home.resolve("AppData/Roaming/NapCat/config/webui.json"));
            paths.add(home.resolve("AppData/Local/NapCat/config/webui.json"));
        }
        for (Path root : FileSystems.getDefault().getRootDirectories()) {
            paths.add(root.resolve("napcat/config/webui.json"));
            paths.add(root.resolve("NapCat/config/webui.json"));
        }
        return List.copyOf(paths);
    }

    /**
     * 根据桌面客户端写入的版本标记定位托管 NapCat 配置。
     * Token 由 NapCat 首次启动时自动生成；Connector 只读取它，不需要用户复制或手工设置。
     */
    private void addManagedRuntimeConfig(Set<Path> paths) {
        String configuredRoot = properties.getManagedRuntimeRoot();
        Path runtimeRoot;
        if (configuredRoot != null && !configuredRoot.isBlank()) {
            runtimeRoot = Path.of(configuredRoot.trim());
        } else {
            String localAppData = System.getenv("LOCALAPPDATA");
            if (localAppData == null || localAppData.isBlank()) {
                return;
            }
            runtimeRoot = Path.of(localAppData, "com.memoecho.desktop", "napcat-runtime");
        }

        String installedVersion = readFile(runtimeRoot.resolve("installed-version")).trim();
        if (!installedVersion.matches("[A-Za-z0-9._-]+")) {
            return;
        }
        paths.add(runtimeRoot
                .resolve(installedVersion)
                .resolve("napcat")
                .resolve("config")
                .resolve("webui.json"));
    }

    /** 将安装目录和 webui.json 文件两种配置写法统一成配置文件路径。 */
    private Path normalizeConfigPath(Path path) {
        if (path.getFileName() == null) {
            return path.resolve("config/webui.json");
        }
        String fileName = path.getFileName().toString();
        if ("webui.json".equalsIgnoreCase(fileName)) {
            return path;
        }
        if ("config".equalsIgnoreCase(fileName)) {
            return path.resolve("webui.json");
        }
        return path.resolve("config/webui.json");
    }

    /** 安全读取本地 NapCat 配置；文件不存在或无权限时直接跳过。 */
    private String readFile(Path path) {
        try {
            return Files.isRegularFile(path) ? Files.readString(path, StandardCharsets.UTF_8) : "";
        } catch (IOException ignored) {
            return "";
        }
    }

    private List<String> containerNames() {
        if (properties.getDockerContainers() == null || properties.getDockerContainers().isBlank()) {
            return List.of();
        }
        return java.util.Arrays.stream(properties.getDockerContainers().split(","))
                .map(String::trim)
                .filter(value -> !value.isBlank())
                .filter(value -> value.matches("[A-Za-z0-9_.-]+"))
                .toList();
    }

    private String runDocker(String... arguments) {
        List<String> command = new ArrayList<>();
        command.add("docker");
        command.addAll(List.of(arguments));
        try {
            Process process = new ProcessBuilder(command).redirectErrorStream(true).start();
            if (!process.waitFor(COMMAND_TIMEOUT.toMillis(), TimeUnit.MILLISECONDS)) {
                process.destroyForcibly();
                return "";
            }
            return new String(process.getInputStream().readAllBytes(), StandardCharsets.UTF_8);
        } catch (IOException | InterruptedException ignored) {
            if (ignored instanceof InterruptedException) {
                Thread.currentThread().interrupt();
            }
            return "";
        }
    }

    String extractToken(String text) {
        if (text == null || text.isBlank()) {
            return "";
        }
        Matcher jsonMatcher = JSON_TOKEN.matcher(text);
        if (jsonMatcher.find()) {
            return jsonMatcher.group(1);
        }
        Matcher urlMatcher = URL_TOKEN.matcher(text);
        return urlMatcher.find() ? urlMatcher.group(1) : "";
    }

    private void addIfPresent(Set<String> candidates, String value) {
        if (value != null && !value.isBlank()) {
            candidates.add(value.trim());
        }
    }
}
