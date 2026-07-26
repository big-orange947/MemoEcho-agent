package com.memoecho.eventcenter.controller;

import org.junit.jupiter.api.Test;

import java.io.IOException;

import static org.junit.jupiter.api.Assertions.assertDoesNotThrow;
import static org.junit.jupiter.api.Assertions.assertFalse;
import static org.junit.jupiter.api.Assertions.assertThrows;
import static org.junit.jupiter.api.Assertions.assertTrue;

class DisconnectedClientExceptionHandlerTest {

    private final DisconnectedClientExceptionHandler handler = new DisconnectedClientExceptionHandler();

    /** 验证 Windows 中文断连消息会被识别并安静处理。 */
    @Test
    void handlesWindowsAbortedConnection() {
        IOException exception = new IOException("你的主机中的软件中止了一个已建立的连接。");

        assertTrue(handler.isClientDisconnect(exception));
        assertDoesNotThrow(() -> handler.handleIOException(exception));
    }

    /** 验证真实的服务端 IO 异常不会被误吞。 */
    @Test
    void rethrowsUnrelatedIOException() {
        IOException exception = new IOException("failed to read local file");

        assertFalse(handler.isClientDisconnect(exception));
        assertThrows(IOException.class, () -> handler.handleIOException(exception));
    }
}
