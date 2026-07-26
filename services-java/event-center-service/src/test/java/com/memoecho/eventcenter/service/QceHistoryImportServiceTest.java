package com.memoecho.eventcenter.service;

import com.fasterxml.jackson.databind.ObjectMapper;
import com.memoecho.eventcenter.dto.QceImportPreviewResponse;
import com.memoecho.eventcenter.dto.QceImportRequest;
import com.memoecho.eventcenter.dto.QceImportResponse;
import com.memoecho.eventcenter.model.StoredEvent;
import com.memoecho.eventcenter.repository.InMemoryEventRecordRepository;
import org.junit.jupiter.api.Test;
import org.springframework.web.server.ResponseStatusException;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertFalse;
import static org.junit.jupiter.api.Assertions.assertThrows;
import static org.junit.jupiter.api.Assertions.assertTrue;
import static org.mockito.Mockito.mock;

/**
 * 验证 QCE 历史导入只写入历史库，并保留非文本媒体的基础引用。
 */
class QceHistoryImportServiceTest {

    private final ObjectMapper objectMapper = new ObjectMapper();

    @Test
    void shouldPreviewAndImportQceJsonWithoutCreatingInboxWork() throws Exception {
        InMemoryEventRecordRepository repository = new InMemoryEventRecordRepository();
        QceHistoryImportService service = new QceHistoryImportService(repository);
        QceImportRequest request = request("""
                {
                  "chatInfo": {
                    "name": "测试好友",
                    "type": "private",
                    "selfUin": "10000",
                    "peerUin": "20000"
                  },
                  "messages": [
                    {
                      "id": "msg-001",
                      "timestamp": 1783800000000,
                      "sender": {"uin": "20000", "name": "小明", "avatarBase64": "aGVsbG8="},
                      "content": {
                        "text": "看一下这张图和文件",
                        "elements": [
                          {"type": "image", "data": {"fileName": "notice.png", "localPath": "resources/images/notice.png"}},
                          {"type": "file", "data": {"fileName": "plan.docx", "localPath": "resources/files/plan.docx"}}
                        ],
                        "resources": []
                      }
                    }
                  ]
                }
                """);

        QceImportPreviewResponse preview = service.preview(request);

        assertEquals("测试好友", preview.chatName());
        assertEquals("private", preview.detectedChatType());
        assertEquals("20000", preview.detectedChatId());
        assertEquals(1, preview.totalMessages());
        assertEquals(1, preview.imageAttachments());
        assertEquals(1, preview.fileAttachments());

        QceImportResponse response = service.importHistory("user-1", request);

        assertEquals(1, response.importedCount());
        assertEquals(2, response.attachmentCount());
        StoredEvent event = repository.findAll().getFirst();
        assertEquals("HISTORY_IMPORT", event.messageOrigin());
        assertEquals("IMPORTED_HISTORY", event.processingStatus());
        assertEquals("SKIPPED", event.writeBackStatus());
        assertEquals("DONE", event.inboxStatus());
        assertFalse(event.needHumanConfirmation());
        assertEquals(2, event.payload().attachments().size());
        assertEquals("resources/images/notice.png", event.payload().attachments().getFirst().url());
        assertTrue(event.payload().rawPayload().path("historyImport").asBoolean());

        EventCenterApplicationService eventCenter = new EventCenterApplicationService(
                repository,
                mock(AgentRuntimeDispatchClient.class),
                mock(QqConnectorMessageClient.class)
        );
        assertEquals(
                "data:image/png;base64,aGVsbG8=",
                eventCenter.findConversationMessages("user-1", "20000", "qq", "private", 10)
                        .getFirst().senderAvatar()
        );
    }

    @Test
    void shouldSkipDuplicateQceMessagesAcrossRepeatedImports() throws Exception {
        InMemoryEventRecordRepository repository = new InMemoryEventRecordRepository();
        QceHistoryImportService service = new QceHistoryImportService(repository);
        QceImportRequest request = request("""
                {
                  "chatInfo": {"name": "测试好友", "type": "private", "peerUin": "20000"},
                  "messages": [{
                    "id": "msg-001",
                    "timestamp": 1783800000000,
                    "sender": {"uin": "20000", "name": "小明"},
                    "content": {"text": "重复导入测试", "elements": [], "resources": []}
                  }]
                }
                """);

        assertEquals(1, service.importHistory("user-1", request).importedCount());
        QceImportResponse repeated = service.importHistory("user-1", request);

        assertEquals(0, repeated.importedCount());
        assertEquals(1, repeated.duplicateCount());
        assertEquals(1, repository.findAll().size());
    }

    @Test
    void shouldInferPrivateSelfIdWhenQceDoesNotExportSelfUin() throws Exception {
        // 这个测试函数的作用是保证 QCE 缺少 selfUin 时，私聊中本人发出的内容仍能被 Runtime 标成“我”。
        InMemoryEventRecordRepository repository = new InMemoryEventRecordRepository();
        QceHistoryImportService service = new QceHistoryImportService(repository);
        QceImportRequest request = request("""
                {
                  "chatInfo": {"name": "测试好友", "type": "private", "peerUin": "20000"},
                  "messages": [
                    {
                      "id": "msg-peer",
                      "timestamp": 1783800000000,
                      "sender": {"uin": "20000", "name": "小明"},
                      "content": {"text": "会员还卖吗", "elements": [], "resources": []}
                    },
                    {
                      "id": "msg-self",
                      "timestamp": 1783800001000,
                      "sender": {"uin": "10000", "name": "freeze"},
                      "content": {"text": "还卖", "elements": [], "resources": []}
                    }
                  ]
                }
                """);

        assertEquals("10000", service.preview(request).selfId());
        service.importHistory("user-1", request);

        assertTrue(repository.findAll().stream()
                .allMatch(event -> "10000".equals(event.payload().selfId())));
    }

    @Test
    void shouldRequireConversationMappingWhenGroupExportHasNoGroupId() throws Exception {
        QceHistoryImportService service = new QceHistoryImportService(new InMemoryEventRecordRepository());
        QceImportRequest request = request("""
                {
                  "chatInfo": {"name": "项目群", "type": "group"},
                  "messages": [{
                    "id": "msg-001",
                    "timestamp": 1783800000000,
                    "sender": {"uin": "20000", "name": "小明"},
                    "content": {"text": "群消息", "elements": [], "resources": []}
                  }]
                }
                """);

        QceImportPreviewResponse preview = service.preview(request);

        assertTrue(preview.requiresChatIdMapping());
        assertThrows(ResponseStatusException.class, () -> service.importHistory("user-1", request));
    }

    /**
     * 将测试 JSON 转成与桌面端一致的请求对象。
     */
    private QceImportRequest request(String json) throws Exception {
        return new QceImportRequest(objectMapper.readTree(json), "qce-test.json", null, null);
    }
}
