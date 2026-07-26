package com.memoecho.connector.qqnapcat.controller;

import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.RestController;

import java.util.Map;

@RestController
public class HealthController {

    /**
     * 返回 Connector 进程健康状态，供本地启动脚本和桌面端判断服务是否已经就绪。
     */
    @GetMapping("/actuator-like/health")
    public Map<String, String> health() {
        return Map.of("status", "UP");
    }
}
