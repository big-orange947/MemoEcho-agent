package com.memoecho.eventcenter.dto;

import java.util.List;

public record SkillResolvePreviewResponse(
        List<SkillDescriptorResponse> resolvedSkills,
        List<String> unresolvedSkillReferences
) {
}
