package com.memoecho.eventcenter.service;

import org.junit.jupiter.api.Test;
import org.springframework.web.servlet.mvc.method.annotation.SseEmitter;

import static org.junit.jupiter.api.Assertions.assertEquals;

class WorkspaceEventStreamServiceTest {

    @Test
    void shouldRegisterSubscriberByPlatformAccount() {
        // 这个测试函数的作用是验证 SSE 订阅按平台账号隔离，避免一个账号的 UI 收到另一个账号的更新。
        WorkspaceEventStreamService service = new WorkspaceEventStreamService();

        SseEmitter emitter = service.subscribe("qq", "3969785168");
        assertEquals(1, service.subscriberCount("qq", "3969785168"));
        assertEquals(0, service.subscriberCount("qq", "other-account"));

        // SseEmitter 的完成回调依赖真实 Servlet 生命周期；连接关闭时的清理逻辑由 onCompletion、onTimeout、onError 注册。
        emitter.complete();
    }
}
