package com.memoecho.eventcenter.controller;

import com.memoecho.eventcenter.dto.EventIngestResponse;
import com.memoecho.eventcenter.dto.StoredEventResponse;
import com.memoecho.eventcenter.dto.UnifiedEventPayload;
import com.memoecho.eventcenter.service.EventCenterApplicationService;
import jakarta.validation.Valid;
import org.springframework.http.HttpStatus;
import org.springframework.http.ResponseEntity;
import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.PathVariable;
import org.springframework.web.bind.annotation.PostMapping;
import org.springframework.web.bind.annotation.RequestBody;
import org.springframework.web.bind.annotation.RequestMapping;
import org.springframework.web.bind.annotation.RestController;

import java.util.List;

@RestController
@RequestMapping("/internal/events")
public class InternalEventController {

    private final EventCenterApplicationService applicationService;

    public InternalEventController(EventCenterApplicationService applicationService) {
        this.applicationService = applicationService;
    }

    @PostMapping("/ingest")
    public ResponseEntity<EventIngestResponse> ingest(@Valid @RequestBody UnifiedEventPayload payload) {
        return ResponseEntity.ok(applicationService.ingest(payload));
    }

    @GetMapping
    public ResponseEntity<List<StoredEventResponse>> list() {
        return ResponseEntity.ok(applicationService.findAll());
    }

    @GetMapping("/{eventId}")
    public ResponseEntity<StoredEventResponse> getByEventId(@PathVariable String eventId) {
        return applicationService.findByEventId(eventId)
                .map(ResponseEntity::ok)
                .orElseGet(() -> ResponseEntity.status(HttpStatus.NOT_FOUND).build());
    }
}
