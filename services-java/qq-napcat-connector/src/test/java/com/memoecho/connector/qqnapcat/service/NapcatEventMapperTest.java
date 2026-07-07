package com.memoecho.connector.qqnapcat.service;

import com.fasterxml.jackson.databind.JsonNode;
import com.fasterxml.jackson.databind.ObjectMapper;
import com.memoecho.connector.qqnapcat.dto.UnifiedEventPayload;
import org.junit.jupiter.api.Test;

import static org.assertj.core.api.Assertions.assertThat;

class NapcatEventMapperTest {

    private final ObjectMapper objectMapper = new ObjectMapper();
    private final NapcatEventMapper mapper = new NapcatEventMapper();

    @Test
    void shouldMapGroupMessageToUnifiedEvent() throws Exception {
        String payload = """
                {
                  "self_id": 3969785168,
                  "user_id": 2597164807,
                  "time": 1780196836,
                  "message_id": 1843661133,
                  "message_type": "group",
                  "sender": {
                    "user_id": 2597164807,
                    "nickname": "freeze",
                    "card": "",
                    "role": "owner"
                  },
                  "raw_message": "今天下午14:00在A01-N105举办分享会",
                  "message": [
                    {
                      "type": "text",
                      "data": {
                        "text": "今天下午14:00在A01-N105举办分享会"
                      }
                    },
                    {
                      "type": "at",
                      "data": {
                        "qq": "3969785168"
                      }
                    }
                  ],
                  "post_type": "message",
                  "group_id": 138178088
                }
                """;

        JsonNode node = objectMapper.readTree(payload);
        UnifiedEventPayload result = mapper.map(node);

        assertThat(result.platform()).isEqualTo("qq");
        assertThat(result.eventType()).isEqualTo("message");
        assertThat(result.chatType()).isEqualTo("group");
        assertThat(result.chatId()).isEqualTo("138178088");
        assertThat(result.sender().id()).isEqualTo("2597164807");
        assertThat(result.sender().name()).isEqualTo("freeze");
        assertThat(result.mentions()).containsExactly("3969785168");
        assertThat(result.text()).contains("14:00");
        assertThat(result.eventId()).isEqualTo("qq:message:group:1843661133");
    }

    @Test
    void shouldExtractAttachmentsFromMessageSegments() throws Exception {
        String payload = """
                {
                  "user_id": 10001,
                  "time": 1780196836,
                  "message_id": 20002,
                  "message_type": "private",
                  "sender": {
                    "user_id": 10001,
                    "nickname": "project-owner"
                  },
                  "message": [
                    {
                      "type": "file",
                      "data": {
                        "file_id": "file-001",
                        "file_name": "activity_notice.xlsx",
                        "url": "http://example.local/file-001"
                      }
                    }
                  ],
                  "post_type": "message"
                }
                """;

        JsonNode node = objectMapper.readTree(payload);
        UnifiedEventPayload result = mapper.map(node);

        assertThat(result.chatType()).isEqualTo("private");
        assertThat(result.attachments()).hasSize(1);
        assertThat(result.attachments().get(0).fileId()).isEqualTo("file-001");
        assertThat(result.attachments().get(0).fileName()).isEqualTo("activity_notice.xlsx");
        assertThat(result.attachments().get(0).fileType()).isEqualTo("file");
    }
}
