package com.memoecho.eventcenter.dto;

/**
 * 面向客户端的 QQ 会话候选项，统一表示好友和群聊，避免暴露 NapCat 原始字段。
 */
public record QqContactResponse(
        String id,
        String name,
        String type,
        String remark
) {
}
