package com.memoecho.eventcenter.controller;

import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.http.ResponseEntity;
import org.springframework.web.bind.annotation.ExceptionHandler;
import org.springframework.web.bind.annotation.RestControllerAdvice;
import org.springframework.web.server.ResponseStatusException;

import java.util.LinkedHashMap;
import java.util.Map;

@RestControllerAdvice
public class ApiResponseStatusExceptionHandler {

    private static final Logger log = LoggerFactory.getLogger(ApiResponseStatusExceptionHandler.class);

    @ExceptionHandler(ResponseStatusException.class)
    public ResponseEntity<Map<String, Object>> handleResponseStatusException(ResponseStatusException exception) {
        // 将业务层已经整理过的安全错误原因返回给客户端，避免界面只能显示笼统的 Bad Gateway。
        String message = exception.getReason() == null || exception.getReason().isBlank()
                ? "请求处理失败"
                : exception.getReason();
        log.warn("接口请求失败，status={}，reason={}", exception.getStatusCode().value(), message);

        Map<String, Object> body = new LinkedHashMap<>();
        body.put("status", exception.getStatusCode().value());
        body.put("error", exception.getStatusCode().toString());
        body.put("message", message);
        return ResponseEntity.status(exception.getStatusCode()).body(body);
    }
}
