package com.memoecho.eventcenter.dto;

import jakarta.validation.constraints.NotBlank;

public record GithubSkillInstallRequest(
        @NotBlank String reference,
        String gitRef
) {
}
