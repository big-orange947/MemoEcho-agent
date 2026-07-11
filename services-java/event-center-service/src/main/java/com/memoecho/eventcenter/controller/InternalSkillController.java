package com.memoecho.eventcenter.controller;

import com.memoecho.eventcenter.dto.GithubSkillInstallRequest;
import com.memoecho.eventcenter.dto.SkillDescriptorResponse;
import com.memoecho.eventcenter.dto.SkillInstallResponse;
import com.memoecho.eventcenter.dto.SkillResolvePreviewRequest;
import com.memoecho.eventcenter.dto.SkillResolvePreviewResponse;
import com.memoecho.eventcenter.service.SkillCatalogApplicationService;
import jakarta.validation.Valid;
import org.springframework.http.ResponseEntity;
import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.PostMapping;
import org.springframework.web.bind.annotation.RequestBody;
import org.springframework.web.bind.annotation.RequestMapping;
import org.springframework.web.bind.annotation.RestController;

import java.util.List;

@RestController
@RequestMapping("/internal/skills")
public class InternalSkillController {

    private final SkillCatalogApplicationService skillCatalogApplicationService;

    public InternalSkillController(SkillCatalogApplicationService skillCatalogApplicationService) {
        // 这个构造函数的作用是注入 skill 目录、安装和预览服务，让 Controller 只负责请求分发与结构返回。
        this.skillCatalogApplicationService = skillCatalogApplicationService;
    }

    @GetMapping
    public ResponseEntity<List<SkillDescriptorResponse>> listSkills() {
        // 这个函数的作用是返回当前内置和已安装的全部 skill，供前端 skill 选择器直接展示。
        return ResponseEntity.ok(skillCatalogApplicationService.listSkills());
    }

    @PostMapping("/install/github")
    public ResponseEntity<SkillInstallResponse> installGithubSkill(@Valid @RequestBody GithubSkillInstallRequest request) {
        // 这个函数的作用是接收 GitHub skill 安装请求，把远程描述文件落盘到本地缓存目录。
        return ResponseEntity.ok(skillCatalogApplicationService.installGithubSkill(request));
    }

    @PostMapping("/resolve-preview")
    public ResponseEntity<SkillResolvePreviewResponse> previewResolve(@RequestBody SkillResolvePreviewRequest request) {
        // 这个函数的作用是让前端在保存会话设定前预览 skill 是否能被当前 route 成功解析并生效。
        return ResponseEntity.ok(skillCatalogApplicationService.previewResolve(request));
    }
}
