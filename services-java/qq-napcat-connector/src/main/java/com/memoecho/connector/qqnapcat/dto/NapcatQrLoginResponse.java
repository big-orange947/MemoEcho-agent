package com.memoecho.connector.qqnapcat.dto;

/**
 * 桌面端扫码登录状态快照。
 * state 使用稳定枚举字符串，避免前端解析 NapCat WebUI 的内部响应结构。
 */
public record NapcatQrLoginResponse(
        String state,
        String qrCodeUrl,
        String message,
        String accountId,
        String accountName,
        boolean onebotConfigured
) {
}
