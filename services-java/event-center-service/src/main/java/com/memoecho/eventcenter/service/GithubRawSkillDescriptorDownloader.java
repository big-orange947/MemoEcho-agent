package com.memoecho.eventcenter.service;

import com.memoecho.eventcenter.model.GithubSkillReference;
import org.springframework.http.HttpStatus;
import org.springframework.stereotype.Service;
import org.springframework.web.server.ResponseStatusException;

import java.io.IOException;
import java.net.URI;
import java.net.http.HttpClient;
import java.net.http.HttpRequest;
import java.net.http.HttpResponse;
import java.time.Duration;

@Service
public class GithubRawSkillDescriptorDownloader implements GithubSkillDescriptorDownloader {

    private final HttpClient httpClient = HttpClient.newBuilder()
            .connectTimeout(Duration.ofSeconds(10))
            .build();

    @Override
    public String downloadSkillDescriptor(GithubSkillReference reference) {
        // 这个函数的作用是从 GitHub raw 地址下载远程 skill 描述文件文本，为“先安装再加载”的安全链路提供输入。
        HttpRequest request = HttpRequest.newBuilder()
                .uri(URI.create(reference.rawDescriptorUrl()))
                .timeout(Duration.ofSeconds(15))
                .GET()
                .build();
        try {
            HttpResponse<String> response = httpClient.send(request, HttpResponse.BodyHandlers.ofString());
            if (response.statusCode() >= 400) {
                throw new ResponseStatusException(
                        HttpStatus.BAD_GATEWAY,
                        "GitHub skill 下载失败，状态码：" + response.statusCode()
                );
            }
            return response.body();
        } catch (IOException | InterruptedException ex) {
            Thread.currentThread().interrupt();
            throw new ResponseStatusException(HttpStatus.BAD_GATEWAY, "GitHub skill 下载失败", ex);
        }
    }
}
