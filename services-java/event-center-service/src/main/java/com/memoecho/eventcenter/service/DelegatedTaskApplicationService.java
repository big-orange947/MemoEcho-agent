package com.memoecho.eventcenter.service;

import com.memoecho.eventcenter.dto.ConversationSummaryResponse;
import com.memoecho.eventcenter.dto.DelegatedTaskCompilationResponse;
import com.memoecho.eventcenter.dto.DelegatedTaskResponse;
import com.memoecho.eventcenter.dto.DelegatedTaskRuntimeUpdateRequest;
import com.memoecho.eventcenter.dto.QqContactResponse;
import com.memoecho.eventcenter.model.DelegatedTask;
import com.memoecho.eventcenter.repository.JdbcDelegatedTaskRepository;
import org.springframework.http.HttpStatus;
import org.springframework.stereotype.Service;
import org.springframework.web.server.ResponseStatusException;

import java.time.Instant;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;
import java.util.Optional;
import java.util.Set;
import java.util.UUID;

/** 负责把自然语言委托编译成受控任务，并管理可跨重启恢复的运行状态。 */
@Service
public class DelegatedTaskApplicationService {

    private static final Set<String> RUNTIME_STATUSES = Set.of("ACTIVE", "COMPLETED", "FAILED");
    private static final Set<String> TERMINAL_STATUSES = Set.of("COMPLETED", "FAILED", "CANCELLED");

    private final DelegatedTaskIntentParser intentParser;
    private final JdbcDelegatedTaskRepository repository;
    private final EventCenterApplicationService eventCenterApplicationService;
    private final QqConnectorContactClient qqConnectorContactClient;
    private final AgentRuntimeDispatchClient runtimeClient;

    /** 注入本地降级解析器、数据库、会话目录与 Python LangGraph 客户端。 */
    public DelegatedTaskApplicationService(
            DelegatedTaskIntentParser intentParser,
            JdbcDelegatedTaskRepository repository,
            EventCenterApplicationService eventCenterApplicationService,
            QqConnectorContactClient qqConnectorContactClient,
            AgentRuntimeDispatchClient runtimeClient
    ) {
        this.intentParser = intentParser;
        this.repository = repository;
        this.eventCenterApplicationService = eventCenterApplicationService;
        this.qqConnectorContactClient = qqConnectorContactClient;
        this.runtimeClient = runtimeClient;
    }

    /**
     * 尝试创建持续委托。LangGraph 负责理解命令，Java 负责校验目标会话和持久化；
     * Runtime 不可用时才退回保守的关键词解析器。
     */
    public Optional<DelegatedTaskResponse> tryCreate(String userId, String command, String requestedRoute) {
        if (requestedRoute != null && !requestedRoute.isBlank()) {
            return Optional.empty();
        }
        String normalizedCommand = command == null ? "" : command.trim();
        List<ConversationSummaryResponse> candidates = loadAuthorizedConversationCandidates(userId);

        DelegatedTaskCompilationResponse compilation = runtimeClient.compileDelegatedTask(
                userId, normalizedCommand, candidates);
        if (compilation != null && compilation.recognized()) {
            DelegatedTask compiledTask = buildCompiledTask(userId, normalizedCommand, compilation, candidates);
            return Optional.of(DelegatedTaskResponse.from(repository.insert(compiledTask)));
        }

        return intentParser.parse(userId, normalizedCommand, candidates)
                .map(this::activateFallbackTask)
                .map(repository::insert)
                .map(DelegatedTaskResponse::from);
    }

    /**
     * Runtime 编译任务前读取当前用户授权过的候选会话。
     * 返回值同时包含本地会话摘要和 NapCat 实时联系人，避免 Agent 只能命中已经有历史消息的会话。
     */
    public List<ConversationSummaryResponse> listAuthorizedConversationCandidates(String userId) {
        return loadAuthorizedConversationCandidates(userId);
    }

    /**
     * Runtime 已经完成自然语言理解后，通过该方法创建任务。
     * 这里仍会复用 buildCompiledTask 的白名单匹配，确保模型返回的 chatId 必须来自当前用户授权会话。
     */
    public DelegatedTaskResponse createCompiled(
            String userId,
            String command,
            DelegatedTaskCompilationResponse compilation
    ) {
        String normalizedCommand = command == null ? "" : command.trim();
        if (normalizedCommand.isBlank()) {
            throw new ResponseStatusException(HttpStatus.BAD_REQUEST, "委托命令不能为空。");
        }
        if (compilation == null || !compilation.recognized()) {
            throw new ResponseStatusException(HttpStatus.BAD_REQUEST, "Runtime 未返回可创建的委托任务。");
        }
        List<ConversationSummaryResponse> candidates = loadAuthorizedConversationCandidates(userId);
        DelegatedTask compiledTask = buildCompiledTask(userId, normalizedCommand, compilation, candidates);
        return DelegatedTaskResponse.from(repository.insert(compiledTask));
    }

    /**
     * 合并数据库中的最近会话和 NapCat 的实时通讯录。
     * 历史摘要提供上下文，实时通讯录负责补齐尚未产生新消息的好友和群聊；Connector 暂时离线时仍可使用本地摘要。
     */
    private List<ConversationSummaryResponse> loadAuthorizedConversationCandidates(String userId) {
        List<ConversationSummaryResponse> summaries = eventCenterApplicationService.findConversationSummariesForUser(
                userId, null, null, null, null, null);
        Map<String, ConversationSummaryResponse> merged = new LinkedHashMap<>();
        for (ConversationSummaryResponse summary : summaries) {
            merged.put(conversationKey(summary.platform(), summary.chatType(), summary.chatId()), summary);
        }
        try {
            for (QqContactResponse contact : qqConnectorContactClient.listContacts(userId)) {
                String chatType = normalizeChatType(contact.type());
                String key = conversationKey("qq", chatType, contact.id());
                merged.put(key, mergeContactWithSummary(contact, chatType, merged.get(key)));
            }
        } catch (ResponseStatusException ignored) {
            // 联系人接口不可用不应阻断委托创建；已有会话摘要仍是当前用户授权范围内的安全候选。
        }
        return List.copyOf(merged.values());
    }

    /** 将实时联系人转换为会话候选，并保留数据库中已有的最近消息和处理状态。 */
    private ConversationSummaryResponse mergeContactWithSummary(
            QqContactResponse contact,
            String chatType,
            ConversationSummaryResponse summary
    ) {
        if (summary == null) {
            return new ConversationSummaryResponse(
                    "qq", chatType, clean(contact.id()), valueOr(contact.name(), contact.id()),
                    "", "", "", "", "", "", "", false, 0, 0, false, false
            );
        }
        return new ConversationSummaryResponse(
                "qq", chatType, clean(contact.id()), valueOr(contact.name(), summary.chatName()),
                summary.lastSenderName(), summary.lastMessage(), summary.lastMessageTime(), summary.lastRoute(),
                summary.lastDispatchMode(), summary.lastProcessingStatus(), summary.lastWriteBackStatus(),
                summary.actionRequired(), summary.unreadLikeCount(), summary.urgentCount(),
                summary.autoReplyEnabled(), summary.summaryEnabled()
        );
    }

    /** 构造跨来源稳定去重键，避免 friend/private 等别名产生重复联系人。 */
    private String conversationKey(String platform, String chatType, String chatId) {
        return clean(platform).toLowerCase() + ":" + normalizeChatType(chatType) + ":" + clean(chatId);
    }

    /** 统一不同连接器使用的私聊和群聊类型名称。 */
    private String normalizeChatType(String chatType) {
        String normalized = clean(chatType).toLowerCase();
        if (Set.of("private", "friend", "direct", "dm").contains(normalized)) {
            return "private";
        }
        if (Set.of("group", "group_chat", "channel").contains(normalized)) {
            return "group";
        }
        return normalized;
    }

    /** 查询当前用户最近的委托任务，供客户端在重启后恢复任务列表。 */
    public List<DelegatedTaskResponse> list(String userId, int limit) {
        List<DelegatedTask> tasks = repository.findRecentByUserId(userId, limit);
        if (tasks.stream().noneMatch(task -> "WAITING_TARGET".equals(task.status()))) {
            return tasks.stream().map(DelegatedTaskResponse::from).toList();
        }
        List<ConversationSummaryResponse> candidates = loadAuthorizedConversationCandidates(userId);
        return tasks.stream()
                .map(task -> retryWaitingTarget(userId, task, candidates))
                .map(DelegatedTaskResponse::from)
                .toList();
    }

    /**
     * 对旧的待选联系人任务做一次保守重试；只有本地解析器找到唯一授权会话时才自动激活。
     */
    private DelegatedTask retryWaitingTarget(
            String userId,
            DelegatedTask task,
            List<ConversationSummaryResponse> candidates
    ) {
        if (!"WAITING_TARGET".equals(task.status())) {
            return task;
        }
        return intentParser.parse(userId, task.originalCommand(), candidates)
                .filter(resolved -> !clean(resolved.chatId()).isBlank())
                .flatMap(resolved -> repository.bindWaitingTarget(task.id(), userId, resolved))
                .orElse(task);
    }

    /**
     * 读取单个委托任务详情，并在仓储层同时校验任务归属，避免跨账号查看任务状态。
     */
    public DelegatedTaskResponse get(String userId, String taskId) {
        return DelegatedTaskResponse.from(requireTask(userId, taskId));
    }

    /** Runtime 按可信会话键读取唯一活动任务，任务状态因此不依赖 Python 进程内存。 */
    public Optional<DelegatedTaskResponse> findActive(
            String userId, String platform, String chatType, String chatId
    ) {
        return repository.findActiveByConversation(userId, clean(platform), clean(chatType), clean(chatId))
                .map(DelegatedTaskResponse::from);
    }

    /** Runtime 每处理完一条事件后幂等提交进度；终态任务禁止恢复为 ACTIVE。 */
    public DelegatedTaskResponse updateRuntime(
            String userId, String taskId, DelegatedTaskRuntimeUpdateRequest request
    ) {
        DelegatedTask current = requireTask(userId, taskId);
        String nextStatus = clean(request.status()).toUpperCase();
        if (!RUNTIME_STATUSES.contains(nextStatus)) {
            throw new ResponseStatusException(HttpStatus.BAD_REQUEST, "不支持的委托任务运行状态。");
        }
        if (TERMINAL_STATUSES.contains(current.status()) && !current.status().equals(nextStatus)) {
            throw new ResponseStatusException(HttpStatus.CONFLICT, "终态委托任务不能重新进入运行态。");
        }
        return repository.updateRuntimeState(
                        taskId, userId, nextStatus, clean(request.progressSummary()),
                        defaultJson(request.stateJson()), clean(request.lastEventId()),
                        clean(request.completionReport()))
                .map(DelegatedTaskResponse::from)
                .orElseThrow(() -> new ResponseStatusException(HttpStatus.NOT_FOUND, "委托任务不存在。"));
    }

    /** 兼容旧客户端的确认按钮；新工作台任务在目标明确时会直接进入 ACTIVE。 */
    public DelegatedTaskResponse confirm(String userId, String taskId) {
        DelegatedTask task = requireTask(userId, taskId);
        if (task.chatId().isBlank()) {
            throw new ResponseStatusException(HttpStatus.CONFLICT, "请先选择目标会话。");
        }
        return repository.updateRuntimeState(taskId, userId, "ACTIVE", "任务已启动", task.stateJson(), "", "")
                .map(DelegatedTaskResponse::from)
                .orElseThrow(() -> new ResponseStatusException(HttpStatus.NOT_FOUND, "委托任务不存在。"));
    }

    /** 用户可以随时取消尚未结束的任务。 */
    public DelegatedTaskResponse cancel(String userId, String taskId) {
        DelegatedTask task = requireTask(userId, taskId);
        if (TERMINAL_STATUSES.contains(task.status())) {
            return DelegatedTaskResponse.from(task);
        }
        return repository.updateRuntimeState(taskId, userId, "CANCELLED", "用户已取消任务",
                        task.stateJson(), task.lastEventId(), "任务由用户取消")
                .map(DelegatedTaskResponse::from)
                .orElseThrow(() -> new ResponseStatusException(HttpStatus.NOT_FOUND, "委托任务不存在。"));
    }

    /**
     * 暂停正在执行的委托。PAUSED 不属于 Runtime 可写状态，只能由已登录用户主动设置。
     */
    public DelegatedTaskResponse pause(String userId, String taskId) {
        DelegatedTask task = requireTask(userId, taskId);
        if (TERMINAL_STATUSES.contains(task.status())) {
            return DelegatedTaskResponse.from(task);
        }
        if (!"ACTIVE".equals(task.status())) {
            throw new ResponseStatusException(HttpStatus.CONFLICT, "只有正在执行的委托任务可以暂停。");
        }
        return repository.updateRuntimeState(taskId, userId, "PAUSED", "任务已由用户暂停",
                        task.stateJson(), task.lastEventId(), task.completionReport())
                .map(DelegatedTaskResponse::from)
                .orElseThrow(() -> new ResponseStatusException(HttpStatus.NOT_FOUND, "委托任务不存在。"));
    }

    /**
     * 继续已暂停的委托，完整保留之前的图状态、已知事实和最后处理事件。
     */
    public DelegatedTaskResponse resume(String userId, String taskId) {
        DelegatedTask task = requireTask(userId, taskId);
        if (!"PAUSED".equals(task.status())) {
            throw new ResponseStatusException(HttpStatus.CONFLICT, "只有已暂停的委托任务可以继续。");
        }
        if (clean(task.chatId()).isBlank()) {
            throw new ResponseStatusException(HttpStatus.CONFLICT, "任务尚未绑定目标会话，无法继续执行。");
        }
        return repository.updateRuntimeState(taskId, userId, "ACTIVE", "任务已继续执行",
                        task.stateJson(), task.lastEventId(), task.completionReport())
                .map(DelegatedTaskResponse::from)
                .orElseThrow(() -> new ResponseStatusException(HttpStatus.NOT_FOUND, "委托任务不存在。"));
    }

    /**
     * 用户明确结束委托时直接进入 COMPLETED，后续会话事件不再被该任务接管。
     */
    public DelegatedTaskResponse complete(String userId, String taskId) {
        DelegatedTask task = requireTask(userId, taskId);
        if (TERMINAL_STATUSES.contains(task.status())) {
            return DelegatedTaskResponse.from(task);
        }
        return repository.updateRuntimeState(taskId, userId, "COMPLETED", "任务已由用户手动结束",
                        task.stateJson(), task.lastEventId(), "用户在控制台手动结束了该委托任务。")
                .map(DelegatedTaskResponse::from)
                .orElseThrow(() -> new ResponseStatusException(HttpStatus.NOT_FOUND, "委托任务不存在。"));
    }

    /** 将 LangGraph 契约转换为数据库对象，并再次验证模型返回的会话归属。 */
    private DelegatedTask buildCompiledTask(
            String userId,
            String command,
            DelegatedTaskCompilationResponse compilation,
            List<ConversationSummaryResponse> candidates
    ) {
        ConversationSummaryResponse target = candidates.stream()
                .filter(item -> clean(item.platform()).equalsIgnoreCase(clean(compilation.platform())))
                .filter(item -> clean(item.chatType()).equalsIgnoreCase(clean(compilation.chatType())))
                .filter(item -> clean(item.chatId()).equals(clean(compilation.chatId())))
                .findFirst()
                .orElse(null);
        boolean resolved = target != null;
        Instant now = Instant.now();
        return new DelegatedTask(
                UUID.randomUUID().toString(), userId, valueOr(compilation.taskType(), "CONVERSATION_GOAL"),
                resolved ? "ACTIVE" : "WAITING_TARGET", command, clean(compilation.targetQuery()),
                resolved ? target.platform() : "", resolved ? target.chatType() : "",
                resolved ? target.chatId() : "", resolved ? target.chatName() : "",
                valueOr(compilation.objective(), command),
                valueOr(compilation.successCriteria(), "目标达成、对方明确拒绝或任务已无法继续"),
                clean(compilation.deadlineText()), compilation.confidence(),
                resolved ? "" : valueOr(compilation.clarificationQuestion(), "需要指定唯一联系人或群聊。"),
                false, "AUTO_COMPLETE", resolved ? valueOr(compilation.initialProgress(), "任务已启动") : "等待选择目标会话",
                defaultJson(compilation.stateJson()), "", resolved ? now : null, null, "", now, now);
    }

    /** 把旧解析器生成的草稿升级为新生命周期，避免 Runtime 不可用时仍要求二次确认。 */
    private DelegatedTask activateFallbackTask(DelegatedTask task) {
        boolean resolved = !clean(task.chatId()).isBlank();
        Instant now = Instant.now();
        return new DelegatedTask(
                task.id(), task.userId(), task.taskType(), resolved ? "ACTIVE" : "WAITING_TARGET",
                task.originalCommand(), task.targetQuery(), task.platform(), task.chatType(), task.chatId(),
                task.targetName(), task.objective(), task.successCriteria(), task.deadlineText(), task.confidence(),
                resolved ? "" : task.clarificationQuestion(), false, "AUTO_COMPLETE",
                resolved ? "任务已通过本地降级解析启动" : "等待选择目标会话", "{}", "",
                resolved ? now : null, null, "", task.createdAt(), now);
    }

    /** 读取任务并校验所有权。 */
    private DelegatedTask requireTask(String userId, String taskId) {
        return repository.findByIdAndUserId(taskId, userId)
                .orElseThrow(() -> new ResponseStatusException(HttpStatus.NOT_FOUND, "委托任务不存在。"));
    }

    private static String clean(String value) {
        return value == null ? "" : value.trim();
    }

    private static String valueOr(String value, String fallback) {
        return clean(value).isBlank() ? fallback : clean(value);
    }

    private static String defaultJson(String value) {
        return clean(value).isBlank() ? "{}" : clean(value);
    }
}
