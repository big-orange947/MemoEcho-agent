package com.memoecho.eventcenter;

import com.memoecho.eventcenter.config.AgentRuntimeDispatchProperties;
import com.memoecho.eventcenter.config.AgentDispatchRetryProperties;
import com.memoecho.eventcenter.config.ConversationProfileSeedProperties;
import com.memoecho.eventcenter.config.DownstreamServiceProperties;
import com.memoecho.eventcenter.config.EventCenterSecurityProperties;
import com.memoecho.eventcenter.config.SkillStoreProperties;
import org.springframework.boot.SpringApplication;
import org.springframework.boot.autoconfigure.SpringBootApplication;
import org.springframework.boot.context.properties.EnableConfigurationProperties;
import org.springframework.scheduling.annotation.EnableScheduling;
import org.springframework.scheduling.annotation.EnableAsync;

@SpringBootApplication
@EnableScheduling
@EnableAsync
@EnableConfigurationProperties({
        AgentDispatchRetryProperties.class,
        AgentRuntimeDispatchProperties.class,
        ConversationProfileSeedProperties.class,
        DownstreamServiceProperties.class,
        EventCenterSecurityProperties.class,
        SkillStoreProperties.class
})
public class EventCenterServiceApplication {

    public static void main(String[] args) {
        SpringApplication.run(EventCenterServiceApplication.class, args);
    }
}
