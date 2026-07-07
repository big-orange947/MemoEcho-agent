package com.memoecho.connector.qqnapcat.service;

import com.fasterxml.jackson.databind.JsonNode;
import com.memoecho.connector.qqnapcat.dto.ConnectorHandleResponse;
import com.memoecho.connector.qqnapcat.dto.EventCenterResponse;
import com.memoecho.connector.qqnapcat.dto.UnifiedEventPayload;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.stereotype.Service;

@Service
public class NapcatEventIngestionService {

    private static final Logger log = LoggerFactory.getLogger(NapcatEventIngestionService.class);

    private final NapcatEventMapper mapper;
    private final EventCenterClient eventCenterClient;

    public NapcatEventIngestionService(NapcatEventMapper mapper, EventCenterClient eventCenterClient) {
        this.mapper = mapper;
        this.eventCenterClient = eventCenterClient;
    }

    public ConnectorHandleResponse handle(JsonNode rawPayload) {
        // 第一步先把 NapCat 原始事件统一映射成仓库内部的标准事件模型。
        UnifiedEventPayload unifiedEvent = mapper.map(rawPayload);
        log.info("Normalized NapCat event: eventId={}, chatType={}, chatId={}, selfId={}, mentions={}, text={}",
                unifiedEvent.eventId(),
                unifiedEvent.chatType(),
                unifiedEvent.chatId(),
                unifiedEvent.selfId(),
                unifiedEvent.mentions(),
                shorten(unifiedEvent.text()));
        // 第二步把标准事件继续转发给 event-center，后面的幂等和分发都在那里完成。
        EventCenterResponse eventCenterResponse = eventCenterClient.forward(unifiedEvent);
        log.info("Forwarded to event center: forwarded={}, httpStatus={}, error={}",
                eventCenterResponse.forwarded(),
                eventCenterResponse.httpStatus(),
                eventCenterResponse.error());
        return new ConnectorHandleResponse(
                unifiedEvent,
                eventCenterResponse,
                "NapCat event normalized and submitted to event center."
        );
    }

    private String shorten(String text) {
        if (text == null || text.isBlank()) {
            return "";
        }
        // 日志里只保留摘要，避免长消息把控制台刷得太难看。
        return text.length() <= 80 ? text : text.substring(0, 80) + "...";
    }
}
