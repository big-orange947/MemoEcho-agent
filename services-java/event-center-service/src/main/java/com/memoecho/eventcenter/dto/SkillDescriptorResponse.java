package com.memoecho.eventcenter.dto;

import java.util.List;

public record SkillDescriptorResponse(
        String id,
        String name,
        String version,
        String type,
        String description,
        String sourceType,
        String reference,
        List<String> applicableRoutes,
        SkillPromptFragmentsResponse promptFragments,
        SkillToolPolicyResponse toolPolicy,
        SkillModelHintsResponse modelHints,
        boolean installed,
        String location
) {
}
