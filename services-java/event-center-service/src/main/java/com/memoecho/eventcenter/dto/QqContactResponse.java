package com.memoecho.eventcenter.dto;

import java.util.List;

/**
 * 面向客户端的 QQ 会话候选项，统一表示好友和群聊，避免暴露 NapCat 原始字段。
 */
public record QqContactResponse(
        String id,
        String name,
        String type,
        String remark,
        List<String> aliases
) {
    /** 兼容旧调用点；新联系人读取链路会显式传入昵称和备注组成的别名。 */
    public QqContactResponse(String id, String name, String type, String remark) {
        this(id, name, type, remark, List.of());
    }

    /** 保证联系人别名集合始终非空且不可变。 */
    public QqContactResponse {
        aliases = aliases == null ? List.of() : List.copyOf(aliases);
    }
}
