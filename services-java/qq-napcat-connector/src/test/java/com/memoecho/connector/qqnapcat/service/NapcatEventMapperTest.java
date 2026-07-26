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
        assertThat(result.actorType()).isEqualTo("CONTACT");
        assertThat(result.platformMessageId()).isEqualTo("1843661133");
        assertThat(result.sequence()).isEqualTo(1843661133L);
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

    @Test
    void shouldUseTargetIdForPrivateSelfMessage() throws Exception {
        // NapCat 上报 message_sent 时 user_id 等于登录账号，target_id 才是当前私聊联系人。
        String payload = """
                {
                  "self_id": 3969785168,
                  "user_id": 3969785168,
                  "target_id": 2597164807,
                  "time": 1783940971,
                  "message_id": 751673566,
                  "message_type": "private",
                  "sender": {
                    "user_id": 3969785168,
                    "nickname": "哈吉仙"
                  },
                  "message": [
                    {
                      "type": "text",
                      "data": {"text": "刚打完原神"}
                    }
                  ],
                  "post_type": "message_sent",
                  "message_sent_type": "self"
                }
                """;

        UnifiedEventPayload result = mapper.map(objectMapper.readTree(payload));

        assertThat(result.eventType()).isEqualTo("message_sent");
        assertThat(result.chatType()).isEqualTo("private");
        assertThat(result.chatId()).isEqualTo("2597164807");
        assertThat(result.selfId()).isEqualTo("3969785168");
        assertThat(result.sender().id()).isEqualTo("3969785168");
    }

    @Test
    void shouldRestoreAgentIdentityForOutboundWebhookEcho() throws Exception {
        // 这个测试函数的作用是保证 Agent 发出的消息回流到 Webhook 后，仍能恢复发送请求的关联身份。
        OutboundMessageRegistry registry = new OutboundMessageRegistry();
        registry.registerPending("private", "2597164807", "一个月15", "client-001", "incoming-001");
        registry.complete(
                "private", "2597164807", "一个月15", "90001", "client-001", "incoming-001"
        );
        NapcatEventMapper correlatedMapper = new NapcatEventMapper(registry);
        String payload = """
                {
                  "self_id": 3969785168,
                  "user_id": 3969785168,
                  "target_id": 2597164807,
                  "time": 1783940971,
                  "message_id": 90001,
                  "message_seq": 77,
                  "message_type": "private",
                  "sender": {
                    "user_id": 3969785168,
                    "nickname": "哈吉仙"
                  },
                  "message": [
                    {
                      "type": "text",
                      "data": {"text": "一个月15"}
                    }
                  ],
                  "post_type": "message_sent",
                  "message_sent_type": "self"
                }
                """;

        UnifiedEventPayload result = correlatedMapper.map(objectMapper.readTree(payload));

        assertThat(result.actorType()).isEqualTo("AGENT");
        assertThat(result.platformMessageId()).isEqualTo("90001");
        assertThat(result.clientMessageId()).isEqualTo("client-001");
        assertThat(result.correlationId()).isEqualTo("incoming-001");
        assertThat(result.sequence()).isEqualTo(77L);
    }
}
