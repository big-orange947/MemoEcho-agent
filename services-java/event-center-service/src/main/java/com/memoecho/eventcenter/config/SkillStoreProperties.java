package com.memoecho.eventcenter.config;

import org.springframework.boot.context.properties.ConfigurationProperties;

@ConfigurationProperties(prefix = "event-center.skills")
public class SkillStoreProperties {

    private String builtinRoot = "../../agent-runtime-python/skills";
    private String installedRoot = "../../agent-runtime-python/skills-installed";
    private String githubDefaultRef = "main";

    public String getBuiltinRoot() {
        // 这个函数的作用是返回仓库内置 skill 描述目录，供前端配置页列出可直接使用的内置 skill。
        return builtinRoot;
    }

    public void setBuiltinRoot(String builtinRoot) {
        // 这个函数的作用是允许通过配置文件覆盖内置 skill 根目录，方便后续拆分仓库或部署时调整路径。
        this.builtinRoot = builtinRoot;
    }

    public String getInstalledRoot() {
        // 这个函数的作用是返回已安装 skill 的缓存目录，GitHub skill 下载后会统一落在这里供 runtime 解析。
        return installedRoot;
    }

    public void setInstalledRoot(String installedRoot) {
        // 这个函数的作用是允许通过配置文件覆盖已安装 skill 的缓存目录，便于本地开发和生产部署切换。
        this.installedRoot = installedRoot;
    }

    public String getGithubDefaultRef() {
        // 这个函数的作用是返回 GitHub skill 在未显式指定分支或标签时使用的默认 ref。
        return githubDefaultRef;
    }

    public void setGithubDefaultRef(String githubDefaultRef) {
        // 这个函数的作用是允许配置默认 GitHub ref，避免代码里写死 main 导致老仓库不兼容。
        this.githubDefaultRef = githubDefaultRef;
    }
}
