package com.memoecho.eventcenter.controller;

import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.web.bind.annotation.ExceptionHandler;
import org.springframework.web.bind.annotation.RestControllerAdvice;
import org.springframework.web.util.DisconnectedClientHelper;

import java.io.IOException;
import java.util.Locale;

@RestControllerAdvice
public class DisconnectedClientExceptionHandler {

    private static final Logger log = LoggerFactory.getLogger(DisconnectedClientExceptionHandler.class);

    /**
     * 处理浏览器、Tauri 客户端关闭 SSE 后产生的写入异常。
     * 这类异常只表示订阅者已经离开，不应让正常的消息入库请求以 ERROR 结束。
     */
    @ExceptionHandler(IOException.class)
    public void handleIOException(IOException exception) throws IOException {
        if (!isClientDisconnect(exception)) {
            throw exception;
        }
        log.debug("SSE 客户端已断开，停止向旧连接写入：{}", exception.getMessage());
    }

    /**
     * 同时识别 Spring 内置断连类型和 Windows 中文系统消息，避免不同系统语言导致漏判。
     */
    boolean isClientDisconnect(Throwable exception) {
        if (DisconnectedClientHelper.isClientDisconnectedException(exception)) {
            return true;
        }
        Throwable current = exception;
        while (current != null) {
            String message = current.getMessage();
            if (message != null) {
                String normalized = message.toLowerCase(Locale.ROOT);
                if (normalized.contains("中止了一个已建立的连接")
                        || normalized.contains("远程主机强迫关闭")
                        || normalized.contains("broken pipe")
                        || normalized.contains("connection reset")) {
                    return true;
                }
            }
            current = current.getCause();
        }
        return false;
    }
}
