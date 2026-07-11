package com.memoecho.eventcenter.dto;

import jakarta.validation.constraints.NotBlank;
import jakarta.validation.constraints.Pattern;
import jakarta.validation.constraints.Size;

public record UserRegisterRequest(
        @NotBlank @Pattern(regexp = "[A-Za-z0-9_.-]{3,64}") String username,
        @NotBlank @Size(min = 8, max = 128) String password,
        @Size(max = 100) String displayName
) {
}
