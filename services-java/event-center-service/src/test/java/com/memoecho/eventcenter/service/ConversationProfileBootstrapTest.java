package com.memoecho.eventcenter.service;

import com.memoecho.eventcenter.config.ConversationProfileSeedProperties;
import com.memoecho.eventcenter.repository.InMemoryConversationProfileRepository;
import org.junit.jupiter.api.Test;
import org.springframework.boot.DefaultApplicationArguments;

import static org.junit.jupiter.api.Assertions.assertEquals;

class ConversationProfileBootstrapTest {

    @Test
    void shouldSeedDefaultProfilesWhenRepositoryIsEmpty() throws Exception {
        // 这个测试函数的作用是验证首次启动时会自动注入 3 条默认演示设定集。
        ConversationProfileSeedProperties properties = new ConversationProfileSeedProperties();
        properties.setSeedDefaults(true);

        ConversationProfileApplicationService applicationService = new ConversationProfileApplicationService(
                new InMemoryConversationProfileRepository()
        );
        ConversationProfileBootstrap bootstrap = new ConversationProfileBootstrap(applicationService, properties);

        bootstrap.run(new DefaultApplicationArguments(new String[0]));

        assertEquals(3, applicationService.listProfiles().size());
    }

    @Test
    void shouldNotSeedProfilesTwice() throws Exception {
        // 这个测试函数的作用是验证重复执行启动引导不会重复插入默认设定集。
        ConversationProfileSeedProperties properties = new ConversationProfileSeedProperties();
        properties.setSeedDefaults(true);

        ConversationProfileApplicationService applicationService = new ConversationProfileApplicationService(
                new InMemoryConversationProfileRepository()
        );
        ConversationProfileBootstrap bootstrap = new ConversationProfileBootstrap(applicationService, properties);

        bootstrap.run(new DefaultApplicationArguments(new String[0]));
        bootstrap.run(new DefaultApplicationArguments(new String[0]));

        assertEquals(3, applicationService.listProfiles().size());
    }
}
