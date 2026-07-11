package com.memoecho.eventcenter.config;

import org.springframework.context.annotation.Configuration;
import org.springframework.web.servlet.config.annotation.CorsRegistry;
import org.springframework.web.servlet.config.annotation.WebMvcConfigurer;

/**
 * 为 Tauri 桌面客户端设置严格的本地 CORS 白名单，而不是放开任意来源。
 */
@Configuration
public class DesktopClientCorsConfiguration implements WebMvcConfigurer {

    private final EventCenterSecurityProperties securityProperties;

    /**
     * 注入桌面客户端来源白名单，使开发与生产环境都可通过配置覆盖。
     */
    public DesktopClientCorsConfiguration(EventCenterSecurityProperties securityProperties) {
        this.securityProperties = securityProperties;
    }

    /**
     * 注册全局 CORS 规则，覆盖登录、连接和模型配置等桌面客户端调用的接口。
     */
    @Override
    public void addCorsMappings(CorsRegistry registry) {
        registry.addMapping("/**")
                .allowedOrigins(securityProperties.getAllowedOrigins().toArray(String[]::new))
                .allowedMethods("GET", "POST", "PUT", "DELETE", "OPTIONS")
                .allowedHeaders("Authorization", "Content-Type", "X-Memo-Echo-User-Id", "X-Memo-Echo-Runtime-Token")
                .maxAge(3600);
    }
}
