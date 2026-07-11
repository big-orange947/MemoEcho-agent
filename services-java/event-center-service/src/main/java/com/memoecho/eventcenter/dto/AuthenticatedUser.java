package com.memoecho.eventcenter.dto;

/** JWT 校验成功后得到的最小用户身份。 */
public record AuthenticatedUser(String userId, String username) {
}
