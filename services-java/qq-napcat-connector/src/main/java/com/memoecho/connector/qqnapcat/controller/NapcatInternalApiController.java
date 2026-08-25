package com.memoecho.connector.qqnapcat.controller;

import com.fasterxml.jackson.databind.JsonNode;
import com.memoecho.connector.qqnapcat.dto.NapcatApiResponse;
import com.memoecho.connector.qqnapcat.dto.GroupOperationRequest;
import com.memoecho.connector.qqnapcat.dto.NapcatLoginInfoData;
import com.memoecho.connector.qqnapcat.dto.NapcatStatusData;
import com.memoecho.connector.qqnapcat.dto.NapcatQrLoginResponse;
import com.memoecho.connector.qqnapcat.dto.NapcatOwnHistoryData;
import com.memoecho.connector.qqnapcat.dto.NapcatPrivateHistoryData;
import com.memoecho.connector.qqnapcat.dto.SendGroupMessageRequest;
import com.memoecho.connector.qqnapcat.dto.SendPrivateMessageRequest;
import com.memoecho.connector.qqnapcat.service.NapcatGroupService;
import com.memoecho.connector.qqnapcat.service.NapcatContactService;
import com.memoecho.connector.qqnapcat.service.NapcatMessageService;
import com.memoecho.connector.qqnapcat.service.NapcatSystemService;
import com.memoecho.connector.qqnapcat.service.NapcatQrLoginService;
import jakarta.validation.Valid;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
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

    private static final Logger log = LoggerFactory.getLogger(NapcatInternalApiController.class);

    private final NapcatMessageService messageService;
    private final NapcatSystemService systemService;
    private final NapcatGroupService groupService;
    private final NapcatContactService contactService;
    private final NapcatQrLoginService qrLoginService;

    public NapcatInternalApiController(
            NapcatMessageService messageService,
            NapcatSystemService systemService,
            NapcatGroupService groupService,
            NapcatContactService contactService,
            NapcatQrLoginService qrLoginService
    ) {
        this.messageService = messageService;
        this.systemService = systemService;
        this.groupService = groupService;
        this.contactService = contactService;
        this.qrLoginService = qrLoginService;
    }

    /** 启动 NapCat 扫码登录，并自动准备 Memo Echo 所需的网络配置。 */
    @PostMapping("/qr-login/start")
    public ResponseEntity<NapcatQrLoginResponse> startQrLogin() {
        return ResponseEntity.ok(qrLoginService.start());
    }

    /** 返回当前二维码、扫码结果与 OneBot 自动配置状态。 */
    @GetMapping("/qr-login/status")
    public ResponseEntity<NapcatQrLoginResponse> getQrLoginStatus() {
        return ResponseEntity.ok(qrLoginService.status());
    }

    /** 二维码失效时主动刷新二维码。 */
    @PostMapping("/qr-login/refresh")
    public ResponseEntity<NapcatQrLoginResponse> refreshQrLogin() {
        return ResponseEntity.ok(qrLoginService.refresh());
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
        return ResponseEntity.ok(messageService.sendGroupMessage(
                request.groupId(), request.toNapcatMessage(), request.clientMessageId(), request.correlationId()));
    }

    @PostMapping("/messages/private")
    public ResponseEntity<NapcatApiResponse<JsonNode>> sendPrivateMessage(
            @Valid @RequestBody SendPrivateMessageRequest request
    ) {
        log.info("sendPrivateMessage received: userId={}, clientMessageId={}, correlationId={}, messageText={}",
                request.userId(), request.clientMessageId(), request.correlationId(),
                request.message() != null ? request.message() : String.valueOf(request.segments()));
        return ResponseEntity.ok(messageService.sendPrivateMessage(
                request.userId(), request.toNapcatMessage(), request.clientMessageId(), request.correlationId()));
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

    /** 返回指定好友会话中由当前登录 QQ 发出的历史消息。 */
    @GetMapping("/friends/{userId}/history/own")
    public ResponseEntity<NapcatApiResponse<NapcatOwnHistoryData>> getOwnFriendHistory(
            @PathVariable String userId,
            @RequestParam(defaultValue = "100") Integer count
    ) {
        return ResponseEntity.ok(contactService.getOwnFriendMessageHistory(userId, count));
    }

    /** 返回指定好友私聊中的完整最近消息，供用户开启的历史上下文同步使用。 */
    @GetMapping("/friends/{userId}/history")
    public ResponseEntity<NapcatApiResponse<NapcatPrivateHistoryData>> getFriendHistory(
            @PathVariable String userId,
            @RequestParam(defaultValue = "100") Integer count
    ) {
        return ResponseEntity.ok(contactService.getFriendMessageHistory(userId, count));
    }

    @GetMapping("/groups/{groupId}/members")
    public ResponseEntity<NapcatApiResponse<JsonNode>> getGroupMemberList(@PathVariable Long groupId) {
        return ResponseEntity.ok(groupService.getGroupMemberList(groupId));
    }

    /** 返回群基础资料。 */
    @GetMapping("/groups/{groupId}")
    public ResponseEntity<NapcatApiResponse<JsonNode>> getGroupInfo(@PathVariable Long groupId) {
        return ResponseEntity.ok(groupService.getGroupInfo(groupId));
    }

    @GetMapping("/groups/{groupId}/history")
    public ResponseEntity<NapcatApiResponse<JsonNode>> getGroupMessageHistory(
            @PathVariable Long groupId,
            @RequestParam(defaultValue = "20") Integer count
    ) {
        // 历史消息能力后面可以给群总结、补拉上下文之类的 Agent 直接复用。
        return ResponseEntity.ok(groupService.getGroupMessageHistory(groupId, count));
    }

    /** 返回群公告列表。 */
    @GetMapping("/groups/{groupId}/notices")
    public ResponseEntity<NapcatApiResponse<JsonNode>> getGroupNotices(@PathVariable Long groupId) {
        return ResponseEntity.ok(groupService.getGroupNotices(groupId));
    }

    /** 返回群精华消息列表。 */
    @GetMapping("/groups/{groupId}/essence-messages")
    public ResponseEntity<NapcatApiResponse<JsonNode>> getEssenceMessages(@PathVariable Long groupId) {
        return ResponseEntity.ok(groupService.getEssenceMessages(groupId));
    }

    /** 返回群根目录文件列表。 */
    @GetMapping("/groups/{groupId}/files")
    public ResponseEntity<NapcatApiResponse<JsonNode>> getGroupFiles(@PathVariable Long groupId) {
        return ResponseEntity.ok(groupService.getRootFiles(groupId));
    }

    /** 返回当前群禁言列表。 */
    @GetMapping("/groups/{groupId}/shut-list")
    public ResponseEntity<NapcatApiResponse<JsonNode>> getGroupShutList(@PathVariable Long groupId) {
        return ResponseEntity.ok(groupService.getShutList(groupId));
    }

    /**
     * 接收 Agent Runtime 已审批的群管理动作。
     * 原始 NapCat action 不对外透传，具体允许范围由 NapcatGroupService 二次校验。
     */
    @PostMapping("/groups/operations")
    public ResponseEntity<NapcatApiResponse<JsonNode>> executeGroupOperation(
            @Valid @RequestBody GroupOperationRequest request
    ) {
        return ResponseEntity.ok(groupService.executeOperation(request));
    }
}
