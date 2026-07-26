package com.memoecho.connector.qqnapcat.dto;

import com.fasterxml.jackson.databind.JsonNode;

import java.util.List;

/** 当前登录 QQ 与指定好友之间的完整私聊历史，不对发送者做过滤。 */
public record NapcatPrivateHistoryData(
        String selfId,
        List<JsonNode> messages
) {
}
