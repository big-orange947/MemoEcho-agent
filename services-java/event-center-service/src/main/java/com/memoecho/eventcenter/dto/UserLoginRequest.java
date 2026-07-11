package com.memoecho.eventcenter.dto;

import jakarta.validation.constraints.NotBlank;

public record UserLoginRequest(@NotBlank String username, @NotBlank String password) {
}
