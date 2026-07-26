package com.memoecho.eventcenter.service;

import com.fasterxml.jackson.databind.JsonNode;
import com.fasterxml.jackson.databind.ObjectMapper;
import com.memoecho.eventcenter.model.ConversationProfile;
import com.memoecho.eventcenter.repository.ConversationProfileRepository;
import com.memoecho.eventcenter.repository.InMemoryEventRecordRepository;
import org.junit.jupiter.api.Test;

import java.util.List;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.mockito.Mockito.mock;
import static org.mockito.Mockito.verify;
import static org.mockito.Mockito.when;

class ConversationHistoryTrainingServiceTest {

    private final ObjectMapper objectMapper = new ObjectMapper();

    @Test
    void shouldTrainOnlySelfAuthoredMessagesFromFullHistory() throws Exception {
        // 该测试验证完整私聊历史中，对方消息只作为上下文，本人消息才进入风格训练。
        ConversationProfile profile = mock(ConversationProfile.class);
        when(profile.id()).thenReturn("profile-1");
        when(profile.userId()).thenReturn("user-1");
        when(profile.privateHistoryEnabled()).thenReturn(true);
        when(profile.historyTrainingEnabled()).thenReturn(true);
        when(profile.platform()).thenReturn("qq");
        when(profile.chatType()).thenReturn("private");
        when(profile.chatIds()).thenReturn(List.of("20000"));

        ConversationProfileRepository profileRepository = mock(ConversationProfileRepository.class);
        when(profileRepository.findByIdAndUserId("profile-1", "user-1")).thenReturn(java.util.Optional.of(profile));
        QqConnectorMessageClient connectorClient = mock(QqConnectorMessageClient.class);
        JsonNode historyResponse = objectMapper.readTree("""
                {
                  "data": {
                    "selfId": "10000",
                    "messages": [
                      {
                        "message_id": "self-message",
                        "time": 1783800000,
                        "user_id": "10000",
                        "sender": {"user_id": "10000", "nickname": "freeze"},
                        "raw_message": "我发出的消息"
                      },
                      {
                        "message_id": "peer-message",
                        "time": 1783800001,
                        "user_id": "20000",
                        "sender": {"user_id": "20000", "nickname": "好友"},
                        "raw_message": "对方发来的消息"
                      }
                    ]
                  }
                }
                """);
        when(connectorClient.fetchPrivateHistory("20000", 100)).thenReturn(historyResponse);
        PersonalSkillAutoPublisher publisher = mock(PersonalSkillAutoPublisher.class);
        when(publisher.evaluate(profile)).thenReturn(
                new PersonalSkillAutoPublisher.PublicationResult(false, "", 1, 0.3)
        );
        InMemoryEventRecordRepository eventRepository = new InMemoryEventRecordRepository();
        ConversationHistoryTrainingService service = new ConversationHistoryTrainingService(
                profileRepository,
                eventRepository,
                connectorClient,
                publisher
        );

        service.syncConversationContext("user-1", "profile-1", 100);

        assertEquals(
                "HISTORY_CONSENTED",
                eventRepository.findByEventId("qq:message:private:self-message").orElseThrow().messageOrigin()
        );
        assertEquals(
                "HISTORY_CONTEXT",
                eventRepository.findByEventId("qq:message:private:peer-message").orElseThrow().messageOrigin()
        );
        verify(publisher).evaluate(profile);
    }
}
