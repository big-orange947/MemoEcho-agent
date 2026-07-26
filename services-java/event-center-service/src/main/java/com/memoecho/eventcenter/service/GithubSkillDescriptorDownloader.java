package com.memoecho.eventcenter.service;

import com.memoecho.eventcenter.model.GithubSkillReference;

public interface GithubSkillDescriptorDownloader {

    String downloadSkillDescriptor(GithubSkillReference reference);

    default String downloadSkillMarkdown(GithubSkillReference reference) {
        // 这个默认函数让旧测试和自定义下载器保持兼容；不支持 SKILL.md 时返回空串，由上层给出明确错误。
        return "";
    }
}
