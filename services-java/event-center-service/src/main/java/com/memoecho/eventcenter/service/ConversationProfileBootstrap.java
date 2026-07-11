package com.memoecho.eventcenter.service;

import com.memoecho.eventcenter.config.ConversationProfileSeedProperties;
import com.memoecho.eventcenter.dto.ConversationProfileUpsertRequest;
import org.springframework.boot.ApplicationArguments;
import org.springframework.boot.ApplicationRunner;
import org.springframework.stereotype.Component;

import java.util.List;

@Component
public class ConversationProfileBootstrap implements ApplicationRunner {

    private final ConversationProfileApplicationService applicationService;
    private final ConversationProfileSeedProperties seedProperties;

    public ConversationProfileBootstrap(
            ConversationProfileApplicationService applicationService,
            ConversationProfileSeedProperties seedProperties
    ) {
        // 这个构造函数的作用是注入设定集应用服务和本地种子配置。
        this.applicationService = applicationService;
        this.seedProperties = seedProperties;
    }

    @Override
    public void run(ApplicationArguments args) {
        // 这个函数的作用是在本地首次启动时自动注入演示设定集，方便联调和前端展示。
        if (!seedProperties.isSeedDefaults()) {
            return;
        }
        if (!applicationService.listProfiles().isEmpty()) {
            return;
        }

        seedPrivateDraftProfile();
        seedAtSelfProfile();
        seedWorkNoticeProfile();
    }

    private void seedPrivateDraftProfile() {
        // 这个函数的作用是创建一个“私聊只出草稿”的默认样例。
        applicationService.createProfile(new ConversationProfileUpsertRequest(
                "QQ 私聊默认草稿模式",
                "适用于一般 QQ 私聊。默认只生成回复草稿，不直接自动发送。",
                true,
                "qq",
                "",
                "life",
                "private",
                List.of(),
                List.of(),
                List.of("social_reply"),
                "ALWAYS",
                List.of(),
                "PROMPT",
                "你是一个克制、简洁、自然的私人助理。优先输出可直接发送的简短回复，不要过度热情。",
                "",
                List.of(),
                "",
                "social_reply",
                "DRAFT_ONLY",
                null,
                null,
                List.of(),
                false,
                5
        ));
    }

    private void seedAtSelfProfile() {
        // 这个函数的作用是创建一个“群聊 @我 时即时响应”的样例。
        applicationService.createProfile(new ConversationProfileUpsertRequest(
                "群聊 @我 时即时响应",
                "适用于 QQ 群聊。只有消息明确 @ 到机器人时才激活，并允许自动回复。",
                true,
                "qq",
                "",
                "",
                "group",
                List.of(),
                List.of(),
                List.of("social_reply", "schedule_extract", "task_plan"),
                "AT_SELF_ONLY",
                List.of(),
                "PROMPT",
                "当你在群聊里被明确点名时，优先回答对方当前问题，保持简短、直接、像一个清醒的工作助手。",
                "",
                List.of(),
                "",
                "social_reply",
                "AUTO_REPLY",
                1,
                3,
                List.of(),
                false,
                20
        ));
    }

    private void seedWorkNoticeProfile() {
        // 这个函数的作用是创建一个“工作群通知监控”样例。
        applicationService.createProfile(new ConversationProfileUpsertRequest(
                "工作群通知监控模式",
                "适用于工作场景群聊。只有命中通知关键词时才激活，并要求人工确认后再决定是否回复。",
                true,
                "qq",
                "",
                "work",
                "group",
                List.of(),
                List.of(),
                List.of("chat_summary", "task_plan", "schedule_extract"),
                "AT_SELF_OR_KEYWORD",
                List.of("通知", "截止", "会议", "安排", "deadline", "meeting"),
                "PROMPT",
                "你是一个任务与通知分拣助手。优先识别是否存在截止时间、会议安排、行动要求和负责人信息。",
                "",
                List.of(),
                "",
                "chat_summary",
                "AUTO_REPLY",
                null,
                null,
                List.of("create_task", "create_schedule"),
                true,
                15
        ));
    }
}
