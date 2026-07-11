package com.memoecho.eventcenter.dto;

public record SkillInstallResponse(
        String status,
        String reference,
        String installedReference,
        String sourceType,
        String targetDirectory,
        SkillDescriptorResponse descriptor
) {
}
