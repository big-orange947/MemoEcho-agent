package com.memoecho.connector.qqnapcat.service;

import com.memoecho.connector.qqnapcat.config.NapcatWebUiProperties;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.io.TempDir;

import java.nio.file.Files;
import java.nio.file.Path;

import static org.assertj.core.api.Assertions.assertThat;

class NapcatWebUiTokenResolverTest {

    @TempDir
    Path temporaryDirectory;

    @Test
    void shouldReadTokenFromNativeNapcatDirectory() throws Exception {
        // 这个测试函数的作用是验证原生 NapCat 安装目录可以被自动读取，用户无需复制 WebUI Token。
        Path configDirectory = temporaryDirectory.resolve("config");
        Files.createDirectories(configDirectory);
        Files.writeString(configDirectory.resolve("webui.json"), "{\"token\":\"native-secret\"}");

        NapcatWebUiProperties properties = new NapcatWebUiProperties();
        properties.setNativeConfigPaths(temporaryDirectory.toString());
        properties.setManagedRuntimeRoot(temporaryDirectory.resolve("missing-managed-runtime").toString());
        properties.setDockerContainers("");

        NapcatWebUiTokenResolver resolver = new NapcatWebUiTokenResolver(properties);

        assertThat(resolver.resolveCandidates()).startsWith("native-secret").contains("napcat");
    }

    @Test
    void shouldExtractTokenFromDockerLog() {
        // 这个测试函数的作用是验证不同版本 Docker 日志中的登录 Token 都能进入候选列表。
        NapcatWebUiTokenResolver resolver = new NapcatWebUiTokenResolver(new NapcatWebUiProperties());

        assertThat(resolver.extractToken("NapCat WebUI Login Token is abc_DEF-123.45"))
                .isEqualTo("abc_DEF-123.45");
    }

    @Test
    void shouldReadTokenFromManagedDesktopRuntime() throws Exception {
        // 这个测试函数的作用是验证桌面客户端安装的 NapCat 可以免配置共享自动生成的 Token。
        Path managedRoot = temporaryDirectory.resolve("napcat-runtime");
        Path configDirectory = managedRoot.resolve("v4.18.9/napcat/config");
        Files.createDirectories(configDirectory);
        Files.writeString(managedRoot.resolve("installed-version"), "v4.18.9\n");
        Files.writeString(configDirectory.resolve("webui.json"), "{\"token\":\"managed-secret\"}");

        NapcatWebUiProperties properties = new NapcatWebUiProperties();
        properties.setManagedRuntimeRoot(managedRoot.toString());
        properties.setDockerContainers("");

        NapcatWebUiTokenResolver resolver = new NapcatWebUiTokenResolver(properties);

        assertThat(resolver.resolveCandidates()).startsWith("managed-secret").contains("napcat");
    }

    @Test
    void shouldIgnoreUnsafeManagedRuntimeVersionMarker() throws Exception {
        // 这个测试函数的作用是防止安装版本标记包含路径跳转，从而读取托管目录之外的文件。
        Path managedRoot = temporaryDirectory.resolve("unsafe-runtime");
        Files.createDirectories(managedRoot);
        Files.writeString(managedRoot.resolve("installed-version"), "../outside");

        NapcatWebUiProperties properties = new NapcatWebUiProperties();
        properties.setManagedRuntimeRoot(managedRoot.toString());
        properties.setDockerContainers("");

        NapcatWebUiTokenResolver resolver = new NapcatWebUiTokenResolver(properties);

        assertThat(resolver.resolveCandidates()).doesNotContain("outside-secret").contains("napcat");
    }
}
