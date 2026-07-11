package com.memoecho.eventcenter.model;

public record GithubSkillReference(
        String originalReference,
        String owner,
        String repository,
        String gitRef,
        String path
) {

    public String rawDescriptorUrl() {
        // 这个函数的作用是把 github:// 风格的 skill 引用转换成 raw.githubusercontent.com 可直接下载的 skill.json 地址。
        return "https://raw.githubusercontent.com/" + owner + "/" + repository + "/" + gitRef + "/" + descriptorPath();
    }

    public String descriptorPath() {
        // 这个函数的作用是统一补齐 skill.json 文件名，让“目录引用”和“文件引用”两种写法最终都落到一个明确的描述文件路径上。
        if (path == null || path.isBlank()) {
            return "skill.json";
        }
        if (path.endsWith(".json")) {
            return path;
        }
        return path + "/skill.json";
    }

    public String installSubdirectory() {
        // 这个函数的作用是生成已安装 skill 在本地缓存目录中的相对路径，保证同一引用每次都会落到同一个目录下。
        String normalizedDescriptorPath = descriptorPath();
        int slashIndex = normalizedDescriptorPath.lastIndexOf('/');
        if (slashIndex < 0) {
            return "github/" + owner + "/" + repository + "/" + gitRef + "/root";
        }
        String folderPath = normalizedDescriptorPath.substring(0, slashIndex);
        if (folderPath.isBlank()) {
            folderPath = "root";
        }
        return "github/" + owner + "/" + repository + "/" + gitRef + "/" + folderPath;
    }
}
