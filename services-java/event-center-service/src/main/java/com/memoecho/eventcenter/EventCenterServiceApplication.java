package com.memoecho.eventcenter;

import com.memoecho.eventcenter.config.AgentRuntimeDispatchProperties;
import org.springframework.boot.SpringApplication;
import org.springframework.boot.autoconfigure.SpringBootApplication;
import org.springframework.boot.context.properties.EnableConfigurationProperties;

@SpringBootApplication
@EnableConfigurationProperties(AgentRuntimeDispatchProperties.class)
public class EventCenterServiceApplication {

    public static void main(String[] args) {
        SpringApplication.run(EventCenterServiceApplication.class, args);
    }
}
