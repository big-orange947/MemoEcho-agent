package com.memoecho.connector.qqnapcat.controller;

import com.fasterxml.jackson.databind.JsonNode;
import com.memoecho.connector.qqnapcat.dto.NapcatApiResponse;
import com.memoecho.connector.qqnapcat.dto.NapcatLoginInfoData;
import com.memoecho.connector.qqnapcat.dto.NapcatStatusData;
import com.memoecho.connector.qqnapcat.dto.SendGroupMessageRequest;
import com.memoecho.connector.qqnapcat.dto.SendPrivateMessageRequest;
import com.memoecho.connector.qqnapcat.service.NapcatGroupService;
import com.memoecho.connector.qqnapcat.service.NapcatContactService;
import com.memoecho.connector.qqnapcat.service.NapcatMessageService;
import com.memoecho.connector.qqnapcat.service.NapcatSystemService;
import jakarta.validation.Valid;
import org.springframework.http.ResponseEntity;
import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.PathVariable;
import org.springframework.web.bind.annotation.PostMapping;
import org.springframework.web.bind.annotation.RequestBody;
import org.springframework.web.bind.annotation.RequestMapping;
import org.springframework.web.bind.annotation.RequestParam;
import org.springframework.web.bind.annotation.RestController;

@RestController
@RequestMapping("/internal/napcat")
public class NapcatInternalApiController {

    private final NapcatMessageService messageService;
    private final NapcatSystemService systemService;
    private final NapcatGroupService groupService;
    private final NapcatContactService contactService;

    public NapcatInternalApiController(
            NapcatMessageService messageService,
            NapcatSystemService systemService,
            NapcatGroupService groupService,
            NapcatContactService contactService
    ) {
        this.messageService = messageService;
        this.systemService = systemService;
        this.groupService = groupService;
        this.contactService = contactService;
    }

    @GetMapping("/login-info")
    public ResponseEntity<NapcatApiResponse<NapcatLoginInfoData>> getLoginInfo() {
        // 对外暴露一个稳定的内部接口，避免 Python 侧直接感知 NapCat 原始地址和鉴权细节。
        return ResponseEntity.ok(systemService.getLoginInfo());
    }

    @GetMapping("/status")
    public ResponseEntity<NapcatApiResponse<NapcatStatusData>> getStatus() {
        return ResponseEntity.ok(systemService.getStatus());
    }

    @PostMapping("/messages/group")
    public ResponseEntity<NapcatApiResponse<JsonNode>> sendGroupMessage(
            @Valid @RequestBody SendGroupMessageRequest request
    ) {
        // 这里统一收口“纯文本”和“消息段”两种发消息方式，调用方不需要再拼 NapCat 原始参数。
        return ResponseEntity.ok(messageService.sendGroupMessage(request.groupId(), request.toNapcatMessage()));
    }

    @PostMapping("/messages/private")
    public ResponseEntity<NapcatApiResponse<JsonNode>> sendPrivateMessage(
            @Valid @RequestBody SendPrivateMessageRequest request
    ) {
        return ResponseEntity.ok(messageService.sendPrivateMessage(request.userId(), request.toNapcatMessage()));
    }

    @GetMapping("/groups")
    public ResponseEntity<NapcatApiResponse<JsonNode>> getGroupList() {
        return ResponseEntity.ok(groupService.getGroupList());
    }

    /**
     * 返回机器人账号的好友列表，供桌面端在设定集中搜索私聊对象。
     */
    @GetMapping("/friends")
    public ResponseEntity<NapcatApiResponse<JsonNode>> getFriendList() {
        return ResponseEntity.ok(contactService.getFriendList());
    }

    @GetMapping("/groups/{groupId}/members")
    public ResponseEntity<NapcatApiResponse<JsonNode>> getGroupMemberList(@PathVariable Long groupId) {
        return ResponseEntity.ok(groupService.getGroupMemberList(groupId));
    }

    @GetMapping("/groups/{groupId}/history")
    public ResponseEntity<NapcatApiResponse<JsonNode>> getGroupMessageHistory(
            @PathVariable Long groupId,
            @RequestParam(defaultValue = "20") Integer count
    ) {
        // 历史消息能力后面可以给群总结、补拉上下文之类的 Agent 直接复用。
        return ResponseEntity.ok(groupService.getGroupMessageHistory(groupId, count));
    }
}
