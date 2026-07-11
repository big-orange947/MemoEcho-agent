package com.memoecho.eventcenter.config;

import org.springframework.boot.context.properties.ConfigurationProperties;

@ConfigurationProperties(prefix = "event-center.conversation-profiles")
public class ConversationProfileSeedProperties {

    private boolean seedDefaults = true;

    public boolean isSeedDefaults() {
        return seedDefaults;
    }

    public void setSeedDefaults(boolean seedDefaults) {
        this.seedDefaults = seedDefaults;
    }
}
