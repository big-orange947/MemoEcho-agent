package com.memoecho.eventcenter.dto;

import java.util.List;

public record ConversationProfileConfigurationResponse(
        List<String> supportedPlatforms,
        List<String> supportedScenes,
        List<String> chatTypes,
        List<String> triggerModes,
        List<String> replyModes,
        List<String> personaModes,
        List<String> supportedRoutes,
        List<String> availableTools,
        List<SkillDescriptorResponse> availableSkills
) {
}
