package com.memoecho.eventcenter.service;

import com.memoecho.eventcenter.model.GithubSkillReference;
import org.springframework.http.HttpStatus;
import org.springframework.stereotype.Service;
import org.springframework.web.server.ResponseStatusException;

import javax.net.ssl.SSLContext;
import javax.net.ssl.TrustManagerFactory;
import java.io.IOException;
import java.net.URI;
import java.net.http.HttpClient;
import java.net.http.HttpRequest;
import java.net.http.HttpResponse;
import java.nio.charset.StandardCharsets;
import java.security.GeneralSecurityException;
import java.security.KeyStore;
import java.time.Duration;
import java.util.Locale;

@Service
public class GithubRawSkillDescriptorDownloader implements GithubSkillDescriptorDownloader {

    private static final int MAX_DOWNLOAD_ATTEMPTS = 3;
    private final HttpClient httpClient = createHttpClient();

    private HttpClient createHttpClient() {
        // 在 Windows 上复用系统根证书，兼容系统已信任但尚未写入 JDK cacerts 的网络证书。
        HttpClient.Builder builder = HttpClient.newBuilder().connectTimeout(Duration.ofSeconds(10));
        if (!System.getProperty("os.name", "").toLowerCase(Locale.ROOT).contains("win")) {
            return builder.build();
        }
        try {
            KeyStore windowsRoot = KeyStore.getInstance("Windows-ROOT");
            windowsRoot.load(null, null);
            TrustManagerFactory trustManagerFactory = TrustManagerFactory.getInstance(
                    TrustManagerFactory.getDefaultAlgorithm()
            );
            trustManagerFactory.init(windowsRoot);
            SSLContext sslContext = SSLContext.getInstance("TLS");
            sslContext.init(null, trustManagerFactory.getTrustManagers(), null);
            return builder.sslContext(sslContext).build();
        } catch (GeneralSecurityException | IOException ex) {
            // 系统证书不可用时保留 JDK 默认校验，绝不降级为信任所有证书。
            return builder.build();
        }
    }

    @Override
    public String downloadSkillDescriptor(GithubSkillReference reference) {
        // 下载 Memo Echo 原生的 skill.json 描述文件。
        return download(reference.rawDescriptorUrl(), "skill.json");
    }

    @Override
    public String downloadSkillMarkdown(GithubSkillReference reference) {
        // 在仓库没有 skill.json 时下载通用 Agent Skills 的 SKILL.md。
        return download(reference.rawMarkdownUrl(), "SKILL.md");
    }

    private String download(String url, String fileName) {
        // 统一处理 GitHub Raw 下载，并对 GitHub 临时限流或网关异常做有限重试。
        HttpRequest request = HttpRequest.newBuilder()
                .uri(URI.create(url))
                .timeout(Duration.ofSeconds(15))
                .header("Accept", "text/plain, application/json;q=0.9, */*;q=0.8")
                .header("User-Agent", "Memo-Echo-Agent-Skill-Installer/1.0")
                .GET()
                .build();
        ResponseStatusException lastFailure = null;
        for (int attempt = 1; attempt <= MAX_DOWNLOAD_ATTEMPTS; attempt++) {
            try {
                HttpResponse<String> response = httpClient.send(
                        request,
                        HttpResponse.BodyHandlers.ofString(StandardCharsets.UTF_8)
                );
                if (response.statusCode() < 400) {
                    return response.body();
                }
                lastFailure = new ResponseStatusException(
                        HttpStatus.BAD_GATEWAY,
                        "GitHub " + fileName + " 下载失败，HTTP " + response.statusCode() + "，地址：" + url
                );
                if (!isRetryableStatus(response.statusCode()) || attempt == MAX_DOWNLOAD_ATTEMPTS) {
                    throw lastFailure;
                }
                pauseBeforeRetry(attempt, fileName);
            } catch (InterruptedException ex) {
                Thread.currentThread().interrupt();
                throw new ResponseStatusException(
                        HttpStatus.BAD_GATEWAY,
                        "GitHub " + fileName + " 下载被中断，地址：" + url,
                        ex
                );
            } catch (IOException ex) {
                lastFailure = new ResponseStatusException(
                        HttpStatus.BAD_GATEWAY,
                        "GitHub " + fileName + " 下载失败：" + ex.getClass().getSimpleName()
                                + (ex.getMessage() == null ? "" : " - " + ex.getMessage()),
                        ex
                );
                if (attempt == MAX_DOWNLOAD_ATTEMPTS) {
                    throw lastFailure;
                }
                pauseBeforeRetry(attempt, fileName);
            }
        }
        throw lastFailure == null
                ? new ResponseStatusException(HttpStatus.BAD_GATEWAY, "GitHub " + fileName + " 下载失败")
                : lastFailure;
    }

    private boolean isRetryableStatus(int statusCode) {
        // 仅重试限流和服务器错误；404 等确定性错误应立即交给 SKILL.md 回退流程。
        return statusCode == 429 || statusCode >= 500;
    }

    private void pauseBeforeRetry(int attempt, String fileName) {
        // 使用很短的递增等待，避免一次 GitHub 瞬时 502 直接导致安装失败。
        try {
            Thread.sleep(250L * attempt);
        } catch (InterruptedException ex) {
            Thread.currentThread().interrupt();
            throw new ResponseStatusException(HttpStatus.BAD_GATEWAY, "等待重试 " + fileName + " 时被中断", ex);
        }
    }
}
