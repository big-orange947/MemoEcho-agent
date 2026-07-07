package com.memoecho.connector.qqnapcat.controller;

import com.fasterxml.jackson.databind.JsonNode;
import com.memoecho.connector.qqnapcat.dto.ConnectorHandleResponse;
import com.memoecho.connector.qqnapcat.service.NapcatEventIngestionService;
import jakarta.validation.Valid;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.http.ResponseEntity;
import org.springframework.web.bind.annotation.PostMapping;
import org.springframework.web.bind.annotation.RequestBody;
import org.springframework.web.bind.annotation.RequestMapping;
import org.springframework.web.bind.annotation.RestController;

@RestController
@RequestMapping("/api/connectors/qq/napcat")
public class NapcatWebhookController {

    private static final Logger log = LoggerFactory.getLogger(NapcatWebhookController.class);

    private final NapcatEventIngestionService ingestionService;

    public NapcatWebhookController(NapcatEventIngestionService ingestionService) {
        this.ingestionService = ingestionService;
    }

    @PostMapping("/events")
    public ResponseEntity<ConnectorHandleResponse> receiveEvent(@Valid @RequestBody JsonNode rawPayload) {
        // 这里是 NapCat 主动推送事件进入系统的第一站，先只做轻量日志和交给 ingestion service。
        log.info("Received NapCat webhook: post_type={}, message_type={}, message_id={}",
                text(rawPayload, "post_type"),
                text(rawPayload, "message_type"),
                text(rawPayload, "message_id"));
        return ResponseEntity.ok(ingestionService.handle(rawPayload));
    }

    private String text(JsonNode node, String fieldName) {
        JsonNode value = node.path(fieldName);
        if (value.isMissingNode() || value.isNull()) {
            return "";
        }
        return value.asText("");
    }
}
