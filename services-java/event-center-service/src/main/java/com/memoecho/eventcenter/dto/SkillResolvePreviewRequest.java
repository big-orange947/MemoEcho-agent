package com.memoecho.eventcenter.dto;

import java.util.List;

public record SkillResolvePreviewRequest(
        List<String> skillReferences,
        String route
) {
}
