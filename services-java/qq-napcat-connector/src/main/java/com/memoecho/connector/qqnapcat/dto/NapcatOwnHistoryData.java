package com.memoecho.connector.qqnapcat.dto;

import com.fasterxml.jackson.databind.JsonNode;

import java.util.List;

/** NapCat 私聊历史中属于当前登录账号的消息集合。 */
public record NapcatOwnHistoryData(
        String selfId,
        List<JsonNode> messages
) {
}
