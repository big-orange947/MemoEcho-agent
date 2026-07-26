package com.memoecho.eventcenter.service;

import com.fasterxml.jackson.databind.ObjectMapper;
import com.memoecho.eventcenter.config.SkillStoreProperties;
import com.memoecho.eventcenter.dto.GithubSkillInstallRequest;
import com.memoecho.eventcenter.dto.SkillInstallResponse;
import com.memoecho.eventcenter.dto.SkillResolvePreviewRequest;
import com.memoecho.eventcenter.dto.SkillResolvePreviewResponse;
import com.memoecho.eventcenter.model.GithubSkillReference;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.io.TempDir;

import java.io.IOException;
import java.nio.file.Files;
import java.nio.file.Path;
import java.util.List;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertFalse;
import static org.junit.jupiter.api.Assertions.assertTrue;

class SkillCatalogApplicationServiceTest {

    @TempDir
    Path tempDir;

    @Test
    void shouldListBuiltinSkillsAndInstalledSkills() throws IOException {
        // 这个测试函数的作用是验证服务会同时扫描内置 skill 和已安装 GitHub skill，供前端配置页统一展示。
        Path builtinRoot = tempDir.resolve("skills");
        Path installedRoot = tempDir.resolve("skills-installed");
        writeSkill(
                builtinRoot.resolve("personas/reliable-assistant/skill.json"),
                """
                        {
                          "id": "persona.reliable_assistant",
                          "name": "可靠助理人格",
                          "type": "persona",
                          "applicableRoutes": ["social_reply"],
                          "promptFragments": { "system": "回复时保持冷静。" },
                          "toolPolicy": { "allow": ["send_qq_message"] },
                          "modelHints": { "temperature": 0.4, "maxTokens": 512 }
                        }
                        """
        );
        writeSkill(
                installedRoot.resolve("github/demo-owner/demo-repo/main/personas/reliable-assistant/skill.json"),
                """
                        {
                          "id": "github.demo.reliable_assistant",
                          "name": "GitHub 可靠助理人格",
                          "type": "persona",
                          "source": "github",
                          "rawReference": "github://demo-owner/demo-repo/personas/reliable-assistant",
                          "applicableRoutes": ["social_reply"],
                          "promptFragments": { "system": "回复时保持冷静。" },
                          "toolPolicy": { "allow": ["send_qq_message"] },
                          "modelHints": { "temperature": 0.4, "maxTokens": 512 }
                        }
                        """
        );

        SkillCatalogApplicationService service = buildService(
                builtinRoot,
                installedRoot,
                reference -> ""
        );

        List<?> skills = service.listSkills();

        assertEquals(2, skills.size());
    }

    @Test
    void shouldInstallGithubSkillAndPreviewResolve() {
        // 这个测试函数的作用是验证 GitHub skill 下载后会写入本地缓存，并能立即参与前端的预览解析。
        Path builtinRoot = tempDir.resolve("skills");
        Path installedRoot = tempDir.resolve("skills-installed");
        SkillCatalogApplicationService service = buildService(
                builtinRoot,
                installedRoot,
                reference -> """
                        {
                          "id": "github.demo.reliable_assistant",
                          "name": "GitHub 可靠助理人格",
                          "type": "persona",
                          "applicableRoutes": ["social_reply"],
                          "promptFragments": { "system": "回复时保持冷静。" },
                          "toolPolicy": { "allow": ["send_qq_message"] },
                          "modelHints": { "temperature": 0.4, "maxTokens": 512 }
                        }
                        """
        );

        SkillInstallResponse installResponse = service.installGithubSkill(
                new GithubSkillInstallRequest("github://demo-owner/demo-repo/personas/reliable-assistant", null)
        );
        SkillResolvePreviewResponse previewResponse = service.previewResolve(
                new SkillResolvePreviewRequest(
                        List.of("github://demo-owner/demo-repo/personas/reliable-assistant"),
                        "social_reply"
                )
        );

        assertEquals("installed", installResponse.status());
        assertTrue(Files.exists(installedRoot.resolve("github/demo-owner/demo-repo/main/personas/reliable-assistant/skill.json")));
        assertEquals(1, previewResponse.resolvedSkills().size());
        assertTrue(previewResponse.unresolvedSkillReferences().isEmpty());
        assertEquals("github://demo-owner/demo-repo@main/personas/reliable-assistant", previewResponse.resolvedSkills().get(0).reference());
        assertFalse(previewResponse.resolvedSkills().get(0).applicableRoutes().isEmpty());
    }

    @Test
    void shouldInstallRootSkillMarkdownFromGithubWebUrl() {
        // 这个测试函数的作用是验证普通 GitHub URL 在没有 skill.json 时会回退解析仓库根目录的 SKILL.md。
        Path builtinRoot = tempDir.resolve("skills");
        Path installedRoot = tempDir.resolve("skills-installed");
        GithubSkillDescriptorDownloader downloader = new GithubSkillDescriptorDownloader() {
            @Override
            public String downloadSkillDescriptor(GithubSkillReference reference) {
                throw new org.springframework.web.server.ResponseStatusException(
                        org.springframework.http.HttpStatus.BAD_REQUEST,
                        "skill.json 不存在"
                );
            }

            @Override
            public String downloadSkillMarkdown(GithubSkillReference reference) {
                return """
                        ---
                        name: 张雪峰风格助手
                        description: |
                          用于模拟鲜明直接的表达风格。
                          只允许使用已有事实。
                        ---
                        回复时观点明确，但不得编造事实。
                        """;
            }
        };
        SkillCatalogApplicationService service = buildService(builtinRoot, installedRoot, downloader);

        SkillInstallResponse response = service.installGithubSkill(
                new GithubSkillInstallRequest("https://github.com/alchaincyf/zhangxuefeng-skill", null)
        );
        SkillResolvePreviewResponse preview = service.previewResolve(
                new SkillResolvePreviewRequest(List.of(response.installedReference()), "social_reply")
        );

        assertEquals("github://alchaincyf/zhangxuefeng-skill@main", response.installedReference());
        assertTrue(Files.exists(installedRoot.resolve("github/alchaincyf/zhangxuefeng-skill/main/root/skill.json")));
        assertEquals(1, preview.resolvedSkills().size());
        assertEquals("张雪峰风格助手", preview.resolvedSkills().get(0).name());
        assertTrue(preview.resolvedSkills().get(0).description().contains("只允许使用已有事实"));
        assertTrue(preview.resolvedSkills().get(0).promptFragments().system().contains("不得编造事实"));
        assertTrue(preview.resolvedSkills().get(0).toolPolicy().allow().isEmpty());
    }

    @Test
    void shouldResolveGeneratedPersonalSkillFromInstalledRoot() throws IOException {
        // 该测试验证自动生成的 personal 引用会从已安装目录解析，而不会被错误地只拿到内置目录查找。
        Path builtinRoot = tempDir.resolve("skills");
        Path installedRoot = tempDir.resolve("skills-installed");
        writeSkill(
                installedRoot.resolve("personal/user-1/friends-style/skill.json"),
                """
                        {
                          "id": "personal.user-1.friends-style",
                          "name": "我的表达风格 · 朋友聊天",
                          "type": "persona",
                          "source": "personal",
                          "rawReference": "personal/user-1/friends-style",
                          "applicableRoutes": ["social_reply"],
                          "promptFragments": { "system": "只模仿表达形式。" },
                          "toolPolicy": { "allow": [] },
                          "modelHints": { "temperature": 0.7, "maxTokens": 256 }
                        }
                        """
        );
        SkillCatalogApplicationService service = buildService(
                builtinRoot,
                installedRoot,
                reference -> ""
        );

        SkillResolvePreviewResponse preview = service.previewResolve(
                new SkillResolvePreviewRequest(List.of("personal/user-1/friends-style"), "social_reply")
        );

        assertEquals(1, preview.resolvedSkills().size());
        assertTrue(preview.unresolvedSkillReferences().isEmpty());
        assertEquals("personal", preview.resolvedSkills().getFirst().sourceType());
        assertEquals("personal/user-1/friends-style", preview.resolvedSkills().getFirst().reference());
    }

    private SkillCatalogApplicationService buildService(
            Path builtinRoot,
            Path installedRoot,
            GithubSkillDescriptorDownloader downloader
    ) {
        // 这个函数的作用是构造完全可控的 skill 服务实例，避免测试依赖真实网络和真实仓库目录。
        SkillStoreProperties properties = new SkillStoreProperties();
        properties.setBuiltinRoot(builtinRoot.toString());
        properties.setInstalledRoot(installedRoot.toString());
        properties.setGithubDefaultRef("main");
        return new SkillCatalogApplicationService(properties, downloader, new ObjectMapper());
    }

    private void writeSkill(Path path, String json) throws IOException {
        // 这个函数的作用是把测试用 skill 描述文件写入临时目录，快速构造扫描与安装场景。
        Files.createDirectories(path.getParent());
        Files.writeString(path, json);
    }
}
