package com.memoecho.eventcenter.service;

import com.memoecho.eventcenter.dto.WorkspaceStreamEventResponse;
import org.springframework.stereotype.Service;
import org.springframework.web.servlet.mvc.method.annotation.SseEmitter;

import java.io.IOException;
import java.util.Set;
import java.util.concurrent.ConcurrentHashMap;
import java.util.concurrent.CopyOnWriteArraySet;

@Service
public class WorkspaceEventStreamService {

    private final ConcurrentHashMap<String, Set<SseEmitter>> emittersByAccount = new ConcurrentHashMap<>();

    public SseEmitter subscribe(String platform, String accountId) {
        // 这个函数的作用是为指定平台账号创建长连接订阅，账号维度隔离不同用户的工作台更新。
        String accountKey = accountKey(platform, accountId);
        SseEmitter emitter = new SseEmitter(0L);
        emittersByAccount.computeIfAbsent(accountKey, ignored -> new CopyOnWriteArraySet<>()).add(emitter);
        emitter.onCompletion(() -> remove(accountKey, emitter));
        emitter.onTimeout(() -> remove(accountKey, emitter));
        emitter.onError(ignored -> remove(accountKey, emitter));
        try {
            emitter.send(SseEmitter.event().name("connected").data("connected"));
        } catch (IOException | IllegalStateException exception) {
            remove(accountKey, emitter);
        }
        return emitter;
    }

    public void publish(WorkspaceStreamEventResponse event) {
        // 这个函数的作用是向拥有该平台账号的所有已连接 UI 广播一条轻量更新事件。
        if (event.accountId() == null || event.accountId().isBlank()) {
            return;
        }
        String accountKey = accountKey(event.platform(), event.accountId());
        Set<SseEmitter> emitters = emittersByAccount.getOrDefault(accountKey, Set.of());
        for (SseEmitter emitter : emitters) {
            try {
                emitter.send(SseEmitter.event().name(event.type()).data(event));
            } catch (IOException | IllegalStateException exception) {
                // 浏览器关闭 SSE 后只移除订阅者；推送失败不能打断消息入库和 Agent 派发。
                remove(accountKey, emitter);
            }
        }
    }

    int subscriberCount(String platform, String accountId) {
        // 这个函数的作用是提供包内可见的订阅数量查询，供测试验证连接清理行为。
        return emittersByAccount.getOrDefault(accountKey(platform, accountId), Set.of()).size();
    }

    private void remove(String accountKey, SseEmitter emitter) {
        Set<SseEmitter> emitters = emittersByAccount.get(accountKey);
        if (emitters == null) {
            return;
        }
        emitters.remove(emitter);
        if (emitters.isEmpty()) {
            emittersByAccount.remove(accountKey, emitters);
        }
    }

    private String accountKey(String platform, String accountId) {
        return (platform == null ? "" : platform.trim().toLowerCase()) + ":" + accountId.trim();
    }
}
