package com.memoecho.connector.qqnapcat.service;

import com.fasterxml.jackson.databind.JsonNode;
import com.memoecho.connector.qqnapcat.dto.GroupOperationRequest;
import com.memoecho.connector.qqnapcat.dto.NapcatApiResponse;
import org.springframework.stereotype.Service;

import java.util.Map;

@Service
public class NapcatGroupService {

    private final NapcatApiClient apiClient;

    public NapcatGroupService(NapcatApiClient apiClient) {
        this.apiClient = apiClient;
    }

    public NapcatApiResponse<JsonNode> getGroupList() {
        // 群列表通常作为“选择可管理群聊”的基础数据来源。
        return apiClient.call("get_group_list", Map.of(), JsonNode.class);
    }

    public NapcatApiResponse<JsonNode> getGroupMemberList(Long groupId) {
        return apiClient.call(
                "get_group_member_list",
                Map.of("group_id", groupId),
                JsonNode.class
        );
    }

    /** 获取群基础资料，供客户端展示群名称、人数和权限状态。 */
    public NapcatApiResponse<JsonNode> getGroupInfo(Long groupId) {
        return apiClient.call("get_group_info", Map.of("group_id", groupId), JsonNode.class);
    }

    public NapcatApiResponse<JsonNode> getGroupMessageHistory(Long groupId, Integer count) {
        // 历史消息读取暂时透传原始结果，后面再按 Agent 需要做结构化。
        return apiClient.call(
                "get_group_msg_history",
                Map.of("group_id", groupId, "count", count),
                JsonNode.class
        );
    }


    /** 获取群公告列表，供通知提取和任务规划使用。 */
    public NapcatApiResponse<JsonNode> getGroupNotices(Long groupId) {
        return apiClient.call("_get_group_notice", Map.of("group_id", groupId), JsonNode.class);
    }

    /** 获取群精华消息列表，精华消息天然具有较高的重要性权重。 */
    public NapcatApiResponse<JsonNode> getEssenceMessages(Long groupId) {
        return apiClient.call("get_essence_msg_list", Map.of("group_id", groupId), JsonNode.class);
    }

    /** 获取群根目录文件，后续可交给文件 Agent 下载、解析和建立索引。 */
    public NapcatApiResponse<JsonNode> getRootFiles(Long groupId) {
        return apiClient.call("get_group_root_files", Map.of("group_id", groupId), JsonNode.class);
    }

    /** 获取群当前禁言列表，只读能力可以安全地用于群状态面板。 */
    public NapcatApiResponse<JsonNode> getShutList(Long groupId) {
        return apiClient.call("get_group_shut_list", Map.of("group_id", groupId), JsonNode.class);
    }

    /**
     * 执行经过 Agent Runtime 审批的群管理动作。
     *
     * <p>该方法是最后一道动作白名单。即使 Python 层被错误提示词影响，也不能借此端点
     * 调用退群、解散群、删除群文件等未开放的高风险接口。</p>
     */
    public NapcatApiResponse<JsonNode> executeOperation(GroupOperationRequest request) {
        String action = request.action().trim().toLowerCase();
        return switch (action) {
            case "mute_member" -> muteMember(request, requireTarget(request), requireDuration(request));
            case "unmute_member" -> muteMember(request, requireTarget(request), 0);
            case "whole_mute" -> apiClient.call(
                    "set_group_whole_ban",
                    Map.of("group_id", request.groupId(), "enable", requireEnable(request)),
                    JsonNode.class
            );
            case "set_member_card" -> apiClient.call(
                    "set_group_card",
                    Map.of(
                            "group_id", request.groupId(),
                            "user_id", requireTarget(request),
                            "card", requireText(request, 60)
                    ),
                    JsonNode.class
            );
            case "set_group_name" -> apiClient.call(
                    "set_group_name",
                    Map.of("group_id", request.groupId(), "group_name", requireText(request, 60)),
                    JsonNode.class
            );
            case "publish_notice" -> apiClient.call(
                    "_send_group_notice",
                    Map.of("group_id", request.groupId(), "content", requireText(request, 3000)),
                    JsonNode.class
            );
            case "set_essence" -> apiClient.call(
                    "set_essence_msg",
                    Map.of("message_id", requireMessageId(request)),
                    JsonNode.class
            );
            case "delete_essence" -> apiClient.call(
                    "delete_essence_msg",
                    Map.of("message_id", requireMessageId(request)),
                    JsonNode.class
            );
            case "kick_member" -> apiClient.call(
                    "set_group_kick",
                    Map.of(
                            "group_id", request.groupId(),
                            "user_id", requireTarget(request),
                            "reject_add_request", Boolean.TRUE.equals(request.rejectAddRequest())
                    ),
                    JsonNode.class
            );
            case "set_admin" -> apiClient.call(
                    "set_group_admin",
                    Map.of(
                            "group_id", request.groupId(),
                            "user_id", requireTarget(request),
                            "enable", requireEnable(request)
                    ),
                    JsonNode.class
            );
            default -> failed("Unsupported group operation: " + action);
        };
    }

    /** 调用 NapCat 单成员禁言接口；duration=0 表示解除禁言。 */
    private NapcatApiResponse<JsonNode> muteMember(GroupOperationRequest request, Long targetUserId, int duration) {
        return apiClient.call(
                "set_group_ban",
                Map.of("group_id", request.groupId(), "user_id", targetUserId, "duration", duration),
                JsonNode.class
        );
    }

    /** 获取必填目标 QQ，避免空值进入 NapCat。 */
    private Long requireTarget(GroupOperationRequest request) {
        if (request.targetUserId() == null) {
            throw new IllegalArgumentException("targetUserId is required for " + request.action());
        }
        return request.targetUserId();
    }

    /** 获取禁言时长并再次执行服务层边界校验。 */
    private int requireDuration(GroupOperationRequest request) {
        if (request.durationSeconds() == null || request.durationSeconds() <= 0) {
            throw new IllegalArgumentException("durationSeconds must be greater than zero");
        }
        return Math.min(request.durationSeconds(), 2_592_000);
    }

    /** 获取布尔开关参数。 */
    private boolean requireEnable(GroupOperationRequest request) {
        if (request.enable() == null) {
            throw new IllegalArgumentException("enable is required for " + request.action());
        }
        return request.enable();
    }

    /** 获取文本参数并限制长度，防止异常大请求进入 QQ。 */
    private String requireText(GroupOperationRequest request, int maxLength) {
        String text = request.text() == null ? "" : request.text().trim();
        if (text.isEmpty()) {
            throw new IllegalArgumentException("text is required for " + request.action());
        }
        if (text.length() > maxLength) {
            throw new IllegalArgumentException("text is too long for " + request.action());
        }
        return text;
    }

    /** 获取精华消息操作需要的消息 ID。 */
    private Long requireMessageId(GroupOperationRequest request) {
        if (request.messageId() == null) {
            throw new IllegalArgumentException("messageId is required for " + request.action());
        }
        return request.messageId();
    }

    /** 用统一 NapCat 响应结构返回白名单拒绝结果。 */
    private NapcatApiResponse<JsonNode> failed(String message) {
        return new NapcatApiResponse<>("failed", -1, null, message, null, null);
    }
}
