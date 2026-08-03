package com.memoecho.eventcenter.service;

import com.memoecho.eventcenter.config.DownstreamServiceProperties;
import com.memoecho.eventcenter.dto.QqContactResponse;
import com.memoecho.eventcenter.model.PlatformConnection;
import com.memoecho.eventcenter.repository.PlatformConnectionRepository;
import org.junit.jupiter.api.Test;
import org.springframework.http.MediaType;
import org.springframework.test.web.client.MockRestServiceServer;
import org.springframework.web.client.RestClient;
import org.springframework.web.server.ResponseStatusException;

import java.time.Instant;
import java.util.List;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertThrows;
import static org.junit.jupiter.api.Assertions.assertTrue;
import static org.mockito.BDDMockito.given;
import static org.mockito.Mockito.mock;
import static org.springframework.test.web.client.match.MockRestRequestMatchers.requestTo;
import static org.springframework.test.web.client.response.MockRestResponseCreators.withSuccess;

class QqConnectorContactClientTest {

    @Test
    void shouldReadFriendsAndGroupsFromCurrentUsersConnector() {
        // 这个测试函数的作用是验证联系人查询使用当前用户保存的 Connector 地址并合并好友与群聊。
        RestClient.Builder builder = RestClient.builder();
        MockRestServiceServer server = MockRestServiceServer.bindTo(builder).build();
        PlatformConnectionRepository repository = mock(PlatformConnectionRepository.class);
        given(repository.findAllByUserId("user-001")).willReturn(List.of(connection("user-001")));
        QqConnectorContactClient client = new QqConnectorContactClient(
                builder.build(), new DownstreamServiceProperties(), repository);

        server.expect(requestTo("http://127.0.0.1:18091/internal/napcat/friends"))
                .andRespond(withSuccess("""
                        {"status":"ok","data":[{"user_id":10001,"nickname":"好友昵称","remark":"好友备注"}]}
                        """, MediaType.APPLICATION_JSON));
        server.expect(requestTo("http://127.0.0.1:18091/internal/napcat/groups"))
                .andRespond(withSuccess("""
                        {"status":"ok","data":[{"group_id":20001,"group_name":"项目群","group_remark":""}]}
                        """, MediaType.APPLICATION_JSON));

        List<QqContactResponse> contacts = client.listContacts("user-001");

        assertEquals(2, contacts.size());
        assertEquals("好友备注", contacts.get(0).name());
        assertEquals("private", contacts.get(0).type());
        assertEquals(List.of("好友备注", "好友昵称", "10001"), contacts.get(0).aliases());
        assertEquals("项目群", contacts.get(1).name());
        assertEquals("group", contacts.get(1).type());
        assertEquals(List.of("项目群", "20001"), contacts.get(1).aliases());
        server.verify();
    }

    @Test
    void shouldExposeNapcatFailureInsteadOfReturningAnEmptyList() {
        // 这个测试函数的作用是防止 NapCat 离线再次被错误展示成“账号没有好友”。
        RestClient.Builder builder = RestClient.builder();
        MockRestServiceServer server = MockRestServiceServer.bindTo(builder).build();
        PlatformConnectionRepository repository = mock(PlatformConnectionRepository.class);
        given(repository.findAllByUserId("user-001")).willReturn(List.of(connection("user-001")));
        QqConnectorContactClient client = new QqConnectorContactClient(
                builder.build(), new DownstreamServiceProperties(), repository);
        server.expect(requestTo("http://127.0.0.1:18091/internal/napcat/friends"))
                .andRespond(withSuccess("""
                        {"status":"failed","message":"I/O error on GET request for 127.0.0.1:3011"}
                        """, MediaType.APPLICATION_JSON));

        ResponseStatusException exception = assertThrows(
                ResponseStatusException.class,
                () -> client.listContacts("user-001")
        );

        assertEquals(502, exception.getStatusCode().value());
        assertTrue(exception.getReason().contains("NapCat 已启动并登录"));
        server.verify();
    }

    /** 创建一个启用的用户级 QQ/NapCat 连接，测试中无需依赖真实数据库。 */
    private PlatformConnection connection(String userId) {
        Instant now = Instant.parse("2026-07-20T00:00:00Z");
        return new PlatformConnection(
                "connection-001", userId, "本地 QQ", "qq", "napcat", true,
                "http://127.0.0.1:18091/", "", "3969785168", "哈吉仙",
                "HEALTHY", "NapCat 已连接。", now, now, now
        );
    }
}
