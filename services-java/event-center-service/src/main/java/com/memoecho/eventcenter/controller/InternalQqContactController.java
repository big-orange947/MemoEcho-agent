package com.memoecho.eventcenter.controller;

import com.memoecho.eventcenter.dto.QqContactResponse;
import com.memoecho.eventcenter.service.LocalUserContextResolver;
import com.memoecho.eventcenter.service.QqConnectorContactClient;
import org.springframework.http.ResponseEntity;
import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.RequestHeader;
import org.springframework.web.bind.annotation.RequestMapping;
import org.springframework.web.bind.annotation.RequestParam;
import org.springframework.web.bind.annotation.RestController;

import java.util.List;
import java.util.Locale;

/**
 * 为桌面客户端提供受登录保护的 QQ 联系人检索接口。
 */
@RestController
@RequestMapping("/internal/contacts/qq")
public class InternalQqContactController {

    private final QqConnectorContactClient contactClient;
    private final LocalUserContextResolver userContextResolver;

    /**
     * 注入联系人读取客户端与认证解析器，禁止未登录页面枚举 QQ 联系人。
     */
    public InternalQqContactController(
            QqConnectorContactClient contactClient,
            LocalUserContextResolver userContextResolver
    ) {
        this.contactClient = contactClient;
        this.userContextResolver = userContextResolver;
    }

    /**
     * 按名称、备注或 QQ 标识筛选好友和群聊，空关键词时返回全部候选项。
     */
    @GetMapping
    public ResponseEntity<List<QqContactResponse>> listContacts(
            @RequestHeader(name = "Authorization", required = false) String authorization,
            @RequestHeader(name = "X-Memo-Echo-User-Id", defaultValue = "local-user") String userId,
            @RequestParam(required = false) String keyword
    ) {
        userContextResolver.resolve(authorization, userId);
        String normalizedKeyword = keyword == null ? "" : keyword.trim().toLowerCase(Locale.ROOT);
        return ResponseEntity.ok(contactClient.listContacts().stream()
                .filter(contact -> matchesKeyword(contact, normalizedKeyword))
                .toList());
    }

    /**
     * 判断联系人是否命中客户端输入的关键词。
     */
    private boolean matchesKeyword(QqContactResponse contact, String keyword) {
        if (keyword.isBlank()) {
            return true;
        }
        return contact.id().toLowerCase(Locale.ROOT).contains(keyword)
                || contact.name().toLowerCase(Locale.ROOT).contains(keyword)
                || contact.remark().toLowerCase(Locale.ROOT).contains(keyword);
    }
}
