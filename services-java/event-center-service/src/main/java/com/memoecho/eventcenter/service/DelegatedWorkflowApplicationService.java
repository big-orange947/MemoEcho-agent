package com.memoecho.eventcenter.service;

import com.fasterxml.jackson.core.JsonProcessingException;
import com.fasterxml.jackson.core.type.TypeReference;
import com.fasterxml.jackson.databind.ObjectMapper;
import com.memoecho.eventcenter.dto.ConversationSummaryResponse;
import com.memoecho.eventcenter.dto.DelegatedTaskCompilationResponse;
import com.memoecho.eventcenter.dto.DelegatedWorkflowCreateRequest;
import com.memoecho.eventcenter.dto.DelegatedWorkflowArtifactRequest;
import com.memoecho.eventcenter.dto.DelegatedWorkflowResponse;
import com.memoecho.eventcenter.dto.DelegatedWorkflowStepCreateRequest;
import com.memoecho.eventcenter.dto.DelegatedWorkflowStepCompleteRequest;
import com.memoecho.eventcenter.dto.DelegatedWorkflowStepResponse;
import com.memoecho.eventcenter.model.DelegatedTask;
import com.memoecho.eventcenter.model.DelegatedWorkflow;
import com.memoecho.eventcenter.repository.JdbcDelegatedTaskRepository;
import com.memoecho.eventcenter.repository.JdbcDelegatedWorkflowRepository;
import com.memoecho.eventcenter.repository.JdbcDelegatedWorkflowStepDispatchRepository;
import org.springframework.http.HttpStatus;
import org.springframework.stereotype.Service;
import org.springframework.transaction.annotation.Transactional;
import org.springframework.web.server.ResponseStatusException;

import java.time.Instant;
import java.util.ArrayDeque;
import java.util.ArrayList;
import java.util.HashMap;
import java.util.HashSet;
import java.util.List;
import java.util.LinkedHashMap;
import java.util.Map;
import java.util.Set;
import java.util.UUID;

/**
 * 管理一条主控台命令对应的父工作流及其有向无环步骤。
 * Python 负责规划，Java 负责权限、依赖、事实来源、幂等和事务持久化。
 */
@Service
public class DelegatedWorkflowApplicationService {

    private static final TypeReference<List<String>> STRING_LIST = new TypeReference<>() { };
    private static final TypeReference<Map<String, Object>> OBJECT_MAP = new TypeReference<>() { };

    private final JdbcDelegatedWorkflowRepository workflowRepository;
    private final JdbcDelegatedTaskRepository taskRepository;
    private final JdbcDelegatedWorkflowStepDispatchRepository dispatchRepository;
    private final DelegatedTaskApplicationService taskApplicationService;
    private final ObjectMapper objectMapper;

    /** 注入工作流、步骤仓储及会话授权查询服务。 */
    public DelegatedWorkflowApplicationService(
            JdbcDelegatedWorkflowRepository workflowRepository,
            JdbcDelegatedTaskRepository taskRepository,
            JdbcDelegatedWorkflowStepDispatchRepository dispatchRepository,
            DelegatedTaskApplicationService taskApplicationService,
            ObjectMapper objectMapper
    ) {
        this.workflowRepository = workflowRepository;
        this.taskRepository = taskRepository;
        this.dispatchRepository = dispatchRepository;
        this.taskApplicationService = taskApplicationService;
        this.objectMapper = objectMapper;
    }

    /**
     * 校验并原子创建父工作流与全部步骤。
     * 根步骤立即激活，存在前置依赖的步骤保持阻塞，等待后续执行器推进。
     */
    @Transactional
    public DelegatedWorkflowResponse create(String userId, DelegatedWorkflowCreateRequest request) {
        String command = required(request.command(), "原始命令不能为空。");
        String executionId = trimToNull(request.executionId());
        if (executionId != null) {
            var existing = workflowRepository.findBySourceExecutionIdAndUserId(executionId, userId);
            if (existing.isPresent()) {
                return toResponse(existing.get());
            }
        }

        List<DelegatedWorkflowStepCreateRequest> steps = List.copyOf(request.steps());
        Map<String, DelegatedWorkflowStepCreateRequest> stepByKey = validateStructure(steps);
        validateFacts(stepByKey);
        validateConversations(userId, steps);

        Instant now = Instant.now();
        String workflowId = UUID.randomUUID().toString();
        DelegatedWorkflow workflow = new DelegatedWorkflow(
                workflowId,
                userId,
                executionId,
                command,
                required(request.title(), "工作流标题不能为空。"),
                defaultText(request.workflowType(), "PLAN_EXECUTE"),
                "RUNNING",
                writeJson(steps),
                "{}",
                "工作流已创建，正在执行根步骤。",
                "",
                now,
                now,
                null
        );
        workflowRepository.insert(workflow);

        for (DelegatedWorkflowStepCreateRequest step : steps) {
            DelegatedTask task = buildTask(userId, workflow, step, now);
            taskRepository.insert(task);
            if ("ACTIVE".equalsIgnoreCase(task.status())) {
                enqueueStepDispatch(task, now);
            }
        }
        return toResponse(workflow);
    }

    /** 按用户和工作流 ID 返回完整父工作流，避免跨账号读取。 */
    public DelegatedWorkflowResponse get(String userId, String workflowId) {
        DelegatedWorkflow workflow = workflowRepository.findByIdAndUserId(workflowId, userId)
                .orElseThrow(() -> new ResponseStatusException(HttpStatus.NOT_FOUND, "工作流不存在。"));
        return toResponse(workflow);
    }

    /** 返回当前用户最近的父工作流，每条主控台命令只占一条记录。 */
    public List<DelegatedWorkflowResponse> list(String userId, int limit) {
        return workflowRepository.findRecentByUserId(userId, limit).stream()
                .map(this::toResponse)
                .toList();
    }

    /**
     * 完成一个已激活步骤，并在同一事务中原子保存类型化产物、合并事实、解锁后继步骤和收口父工作流。
     * 父工作流行锁会串行化并发回调；重复提交已完成步骤时直接返回当前状态，不重复产生副作用。
     */
    @Transactional
    public DelegatedWorkflowResponse completeStep(
            String userId,
            String workflowId,
            String stepKey,
            DelegatedWorkflowStepCompleteRequest request
    ) {
        DelegatedWorkflow workflow = workflowRepository.findByIdAndUserIdForUpdate(workflowId, userId)
                .orElseThrow(() -> new ResponseStatusException(HttpStatus.NOT_FOUND, "工作流不存在。"));
        List<DelegatedTask> tasks = taskRepository.findByWorkflowId(workflowId);
        DelegatedTask current = findStep(tasks, stepKey);
        if ("COMPLETED".equalsIgnoreCase(current.status())) {
            return toResponse(workflow);
        }
        if (!"RUNNING".equalsIgnoreCase(workflow.status())) {
            throw conflict("工作流已结束，不能继续完成步骤。");
        }
        if (!"ACTIVE".equalsIgnoreCase(current.status())) {
            throw conflict("步骤尚未激活，不能提前完成：" + stepKey);
        }

        List<DelegatedWorkflowArtifactRequest> artifacts = safeArtifacts(request.artifacts());
        Map<String, Object> producedFacts = producedFactsFromArtifacts(current, artifacts, request.producedFacts());
        validateProducedFacts(current, producedFacts);
        Map<String, Object> mergedFacts = new LinkedHashMap<>(readObjectMap(workflow.factsJson()));
        mergedFacts.putAll(producedFacts);

        Instant now = Instant.now();
        String summary = defaultText(request.resultSummary(), "步骤已完成。");
        Map<String, Object> persistedResult = new LinkedHashMap<>();
        persistedResult.put("summary", summary);
        persistedResult.put("artifacts", artifacts);
        persistedResult.put("producedFacts", producedFacts);
        persistedResult.put("result", request.result());
        int completed = taskRepository.completeWorkflowStep(
                workflowId, current.stepKey(), userId, writeJson(persistedResult), summary, now);
        if (completed == 0) {
            DelegatedTask latest = findStep(taskRepository.findByWorkflowId(workflowId), stepKey);
            if ("COMPLETED".equalsIgnoreCase(latest.status())) {
                return toResponse(workflowRepository.findByIdAndUserId(workflowId, userId).orElse(workflow));
            }
            throw conflict("步骤状态已变化，请刷新后重试。");
        }

        List<DelegatedTask> afterCompletion = taskRepository.findByWorkflowId(workflowId);
        // 触发本次完成的事件成为后继步骤的起点水位，让后继步骤只把起点之后的证据纳入 L1。
        activateReadySteps(userId, workflowId, afterCompletion, mergedFacts, now, request.sourceEventId());
        List<DelegatedTask> latestTasks = taskRepository.findByWorkflowId(workflowId);
        boolean allCompleted = !latestTasks.isEmpty()
                && latestTasks.stream().allMatch(task -> "COMPLETED".equalsIgnoreCase(task.status()));
        long completedCount = latestTasks.stream()
                .filter(task -> "COMPLETED".equalsIgnoreCase(task.status()))
                .count();
        String workflowStatus = allCompleted ? "COMPLETED" : "RUNNING";
        String progress = allCompleted
                ? "工作流全部步骤已完成。"
                : "已完成 " + completedCount + "/" + latestTasks.size() + " 个步骤。";
        workflowRepository.updateRuntimeState(
                workflowId, userId, workflowStatus, writeJson(mergedFacts), progress, "", now,
                allCompleted ? now : null);
        DelegatedWorkflow latestWorkflow = workflowRepository.findByIdAndUserId(workflowId, userId)
                .orElseThrow(() -> new ResponseStatusException(HttpStatus.NOT_FOUND, "工作流不存在。"));
        return toResponse(latestWorkflow);
    }

    /** 找到指定步骤；不存在时返回明确的 404，而不是让 Runtime 误判为执行成功。 */
    private DelegatedTask findStep(List<DelegatedTask> tasks, String stepKey) {
        String normalizedKey = required(stepKey, "步骤键不能为空。");
        return tasks.stream()
                .filter(task -> normalizedKey.equals(task.stepKey()))
                .findFirst()
                .orElseThrow(() -> new ResponseStatusException(HttpStatus.NOT_FOUND, "工作流步骤不存在。"));
    }

    /** 校验 Runtime 只能写入该步骤声明的事实，并保证所有声明输出均已提供。 */
    private void validateProducedFacts(DelegatedTask task, Map<String, Object> producedFacts) {
        Set<String> declared = Set.copyOf(readStringList(task.producesFactsJson()));
        for (String key : producedFacts.keySet()) {
            if (!declared.contains(key)) {
                throw badRequest("步骤 " + task.stepKey() + " 产生了未声明事实：" + key);
            }
        }
        for (String key : declared) {
            if (!producedFacts.containsKey(key)) {
                throw badRequest("步骤 " + task.stepKey() + " 缺少声明事实：" + key);
            }
        }
    }

    /** 复制并校验类型化产物，保证 name 非空、不重复，且不能与声明事实冲突。 */
    private List<DelegatedWorkflowArtifactRequest> safeArtifacts(List<DelegatedWorkflowArtifactRequest> artifacts) {
        if (artifacts == null || artifacts.isEmpty()) {
            return List.of();
        }
        List<DelegatedWorkflowArtifactRequest> normalized = new ArrayList<>();
        Set<String> names = new HashSet<>();
        for (DelegatedWorkflowArtifactRequest artifact : artifacts) {
            String name = trimToNull(artifact.name());
            if (name == null) {
                throw badRequest("类型化产物缺少事实名称。");
            }
            String type = trimToNull(artifact.type());
            if (type == null) {
                throw badRequest("类型化产物 " + name + " 缺少类型。");
            }
            if (!names.add(name)) {
                throw badRequest("类型化产物事实名称重复：" + name);
            }
            normalized.add(new DelegatedWorkflowArtifactRequest(
                    type, name, artifact.value(), trimToNull(artifact.sourceEventId())));
        }
        return normalized;
    }

    /**
     * 优先从类型化产物派生事实映射；未提供产物时回退到旧的 producedFacts 契约。
     * 产物存在时以其 name→value 为准，避免同一事实出现两套值。
     */
    private Map<String, Object> producedFactsFromArtifacts(
            DelegatedTask task,
            List<DelegatedWorkflowArtifactRequest> artifacts,
            Map<String, Object> producedFacts
    ) {
        if (artifacts.isEmpty()) {
            return normalizeFacts(producedFacts);
        }
        Map<String, Object> derived = new LinkedHashMap<>();
        for (DelegatedWorkflowArtifactRequest artifact : artifacts) {
            if (!readStringList(task.producesFactsJson()).contains(artifact.name())) {
                throw badRequest("步骤 " + task.stepKey() + " 的产物未在声明事实中：" + artifact.name());
            }
            derived.put(artifact.name(), artifact.value());
        }
        return derived;
    }

    /** 仅激活依赖全部完成且所需事实全部存在的阻塞步骤。 */
    private void activateReadySteps(
            String userId,
            String workflowId,
            List<DelegatedTask> tasks,
            Map<String, Object> facts,
            Instant now,
            String sourceEventId
    ) {
        Map<String, DelegatedTask> taskByKey = new HashMap<>();
        tasks.forEach(task -> taskByKey.put(task.stepKey(), task));
        for (DelegatedTask task : tasks) {
            if (!"BLOCKED".equalsIgnoreCase(task.status())) {
                continue;
            }
            boolean dependenciesCompleted = readStringList(task.dependsOnJson()).stream()
                    .map(taskByKey::get)
                    .allMatch(dependency -> dependency != null
                            && "COMPLETED".equalsIgnoreCase(dependency.status()));
            boolean factsReady = readStringList(task.requiredFactsJson()).stream().allMatch(facts::containsKey);
            if (dependenciesCompleted && factsReady) {
                int activated = taskRepository.activateWorkflowStep(
                        workflowId, task.stepKey(), userId, "前置步骤与事实已就绪。", now, sourceEventId);
                if (activated == 1) {
                    dispatchRepository.enqueue(
                            task.workflowId(), task.stepKey(), task.activationVersion() + 1,
                            task.id(), task.userId(), now);
                }
            }
        }
    }

    /**
     * 将一个已激活步骤写入可靠投递表。
     * 该方法必须由创建或激活步骤的事务调用，确保任务状态与待执行记录不会只成功一半。
     */
    private void enqueueStepDispatch(DelegatedTask task, Instant now) {
        dispatchRepository.enqueue(
                task.workflowId(), task.stepKey(), task.activationVersion(),
                task.id(), task.userId(), now);
    }

    /** 复制 Runtime 事实，过滤空键并保持插入顺序，避免修改请求对象。 */
    private Map<String, Object> normalizeFacts(Map<String, Object> facts) {
        Map<String, Object> normalized = new LinkedHashMap<>();
        if (facts == null) {
            return normalized;
        }
        facts.forEach((key, value) -> {
            String normalizedKey = trimToNull(key);
            if (normalizedKey == null) {
                throw badRequest("事实键不能为空。");
            }
            normalized.put(normalizedKey, value);
        });
        return normalized;
    }

    /** 将父工作流事实 JSON 恢复为映射，损坏数据必须显式报错以免错误解锁步骤。 */
    private Map<String, Object> readObjectMap(String json) {
        if (json == null || json.isBlank()) {
            return Map.of();
        }
        try {
            return objectMapper.readValue(json, OBJECT_MAP);
        } catch (JsonProcessingException exception) {
            throw new ResponseStatusException(HttpStatus.INTERNAL_SERVER_ERROR, "工作流事实数据已损坏。", exception);
        }
    }

    /** 校验步骤键、依赖引用和 DAG，返回后续校验可复用的步骤索引。 */
    private Map<String, DelegatedWorkflowStepCreateRequest> validateStructure(
            List<DelegatedWorkflowStepCreateRequest> steps
    ) {
        Map<String, DelegatedWorkflowStepCreateRequest> stepByKey = new HashMap<>();
        for (DelegatedWorkflowStepCreateRequest step : steps) {
            String key = required(step.stepKey(), "步骤键不能为空。");
            if (step.compilation() == null || !step.compilation().recognized()) {
                throw badRequest("步骤 " + key + " 未被 Runtime 正确识别。");
            }
            if (stepByKey.putIfAbsent(key, step) != null) {
                throw badRequest("步骤键重复：" + key);
            }
        }
        for (DelegatedWorkflowStepCreateRequest step : steps) {
            for (String dependency : safeList(step.dependsOn())) {
                if (step.stepKey().equals(dependency)) {
                    throw badRequest("步骤不能依赖自身：" + step.stepKey());
                }
                if (!stepByKey.containsKey(dependency)) {
                    throw badRequest("步骤依赖不存在：" + dependency);
                }
            }
        }
        ensureAcyclic(stepByKey);
        return stepByKey;
    }

    /** 使用拓扑遍历验证依赖图不存在环。 */
    private void ensureAcyclic(Map<String, DelegatedWorkflowStepCreateRequest> stepByKey) {
        Map<String, Integer> indegree = new HashMap<>();
        Map<String, List<String>> successors = new HashMap<>();
        stepByKey.keySet().forEach(key -> indegree.put(key, 0));
        stepByKey.values().forEach(step -> safeList(step.dependsOn()).forEach(dependency -> {
            indegree.compute(step.stepKey(), (key, value) -> value == null ? 1 : value + 1);
            successors.computeIfAbsent(dependency, ignored -> new ArrayList<>()).add(step.stepKey());
        }));
        ArrayDeque<String> queue = new ArrayDeque<>();
        indegree.forEach((key, value) -> {
            if (value == 0) {
                queue.add(key);
            }
        });
        int visited = 0;
        while (!queue.isEmpty()) {
            String key = queue.removeFirst();
            visited++;
            for (String successor : successors.getOrDefault(key, List.of())) {
                int remaining = indegree.computeIfPresent(successor, (ignored, value) -> value - 1);
                if (remaining == 0) {
                    queue.addLast(successor);
                }
            }
        }
        if (visited != stepByKey.size()) {
            throw badRequest("步骤依赖存在环，无法创建工作流。");
        }
    }

    /**
     * 校验每个步骤所需事实均由祖先步骤产生。
     * 这能阻止“先通知小号、再询问 km”这类依赖顺序错误。
     */
    private void validateFacts(Map<String, DelegatedWorkflowStepCreateRequest> stepByKey) {
        for (DelegatedWorkflowStepCreateRequest step : stepByKey.values()) {
            Set<String> available = new HashSet<>();
            collectAncestorFacts(step, stepByKey, new HashSet<>(), available);
            for (String requiredFact : safeList(step.requiredFacts())) {
                if (!available.contains(requiredFact)) {
                    throw badRequest("步骤 " + step.stepKey() + " 缺少事实来源：" + requiredFact);
                }
            }
        }
    }

    /** 递归收集当前步骤所有祖先产生的事实。 */
    private void collectAncestorFacts(
            DelegatedWorkflowStepCreateRequest step,
            Map<String, DelegatedWorkflowStepCreateRequest> stepByKey,
            Set<String> visited,
            Set<String> facts
    ) {
        for (String dependency : safeList(step.dependsOn())) {
            if (!visited.add(dependency)) {
                continue;
            }
            DelegatedWorkflowStepCreateRequest ancestor = stepByKey.get(dependency);
            facts.addAll(safeList(ancestor.producesFacts()));
            collectAncestorFacts(ancestor, stepByKey, visited, facts);
        }
    }

    /** 确保模型选择的目标会话确实属于当前用户可访问的联系人或群聊。 */
    private void validateConversations(String userId, List<DelegatedWorkflowStepCreateRequest> steps) {
        List<ConversationSummaryResponse> candidates = taskApplicationService
                .listAuthorizedConversationCandidates(userId);
        for (DelegatedWorkflowStepCreateRequest step : steps) {
            DelegatedTaskCompilationResponse compilation = step.compilation();
            String chatId = trimToNull(compilation.chatId());
            boolean referencesConversation = chatId != null
                    || trimToNull(compilation.targetQuery()) != null
                    || trimToNull(compilation.targetName()) != null;
            if (!referencesConversation) {
                continue;
            }
            if (chatId == null) {
                throw badRequest("步骤 " + step.stepKey() + " 尚未解析出目标会话。");
            }
            boolean authorized = candidates.stream().anyMatch(candidate ->
                    same(candidate.platform(), compilation.platform())
                            && same(candidate.chatType(), compilation.chatType())
                            && same(candidate.chatId(), chatId));
            if (!authorized) {
                throw badRequest("步骤 " + step.stepKey() + " 的目标会话未获授权。");
            }
        }
    }

    /** 将已校验步骤转换为可持久化的委托任务。 */
    private DelegatedTask buildTask(
            String userId,
            DelegatedWorkflow workflow,
            DelegatedWorkflowStepCreateRequest step,
            Instant now
    ) {
        DelegatedTaskCompilationResponse compilation = step.compilation();
        boolean root = safeList(step.dependsOn()).isEmpty();
        return new DelegatedTask(
                UUID.randomUUID().toString(), workflow.id(), step.stepKey(), step.order(), step.role(),
                step.instruction(), writeJson(safeList(step.dependsOn())),
                writeJson(safeList(step.requiredFacts())), writeJson(safeList(step.producesFacts())),
                "{}", root ? 1L : 0L, userId, defaultText(compilation.taskType(), "WORKFLOW_STEP"),
                root ? "ACTIVE" : "BLOCKED", workflow.originalCommand(),
                workflow.sourceExecutionId() == null
                        ? workflow.id() + ":" + step.stepKey()
                        : workflow.sourceExecutionId() + ":" + step.stepKey(),
                compilation.targetQuery(), compilation.platform(), compilation.chatType(), compilation.chatId(),
                compilation.targetName(), compilation.objective(), compilation.successCriteria(),
                compilation.deadlineText(), compilation.confidence(), compilation.clarificationQuestion(),
                compilation.requiresConfirmation(), defaultText(compilation.executionMode(), "AUTO_COMPLETE"),
                root ? defaultText(compilation.initialProgress(), "步骤已激活。") : "等待前置步骤完成。",
                defaultText(compilation.stateJson(), "{}"), "", root ? trimToNull(step.startEventId()) : null,
                buildConversationScopeJson(compilation), root ? now : null,
                null, "", now, now
        );
    }

    /**
     * 把步骤的目标会话固化为历史查询使用的会话范围。
     * 私聊会话的 chatId 固定为对方平台账号，绝不使用 Agent 自身账号或临时推导值。
     */
    private String buildConversationScopeJson(DelegatedTaskCompilationResponse compilation) {
        Map<String, String> scope = new LinkedHashMap<>();
        scope.put("platform", defaultText(compilation.platform(), ""));
        scope.put("chatType", defaultText(compilation.chatType(), ""));
        scope.put("chatId", defaultText(compilation.chatId(), ""));
        return writeJson(scope);
    }

    /** 将父工作流与排序后的步骤聚合为 API 响应。 */
    private DelegatedWorkflowResponse toResponse(DelegatedWorkflow workflow) {
        List<DelegatedWorkflowStepResponse> steps = taskRepository.findByWorkflowId(workflow.id()).stream()
                .map(task -> new DelegatedWorkflowStepResponse(
                        task.id(), task.stepKey(), task.stepOrder(), task.stepRole(), task.stepInstruction(),
                        readStringList(task.dependsOnJson()), readStringList(task.requiredFactsJson()),
                        readStringList(task.producesFactsJson()), task.status(), task.activationVersion(),
                        task.targetName(), task.platform(),
                        task.chatType(), task.chatId(), task.objective(), task.progressSummary(),
                        task.startedAt(), task.completedAt(), task.startEventId()))
                .toList();
        return DelegatedWorkflowResponse.from(workflow, steps);
    }

    /** 将 JSON 数组恢复为不可变字符串列表，旧数据异常时安全降级为空列表。 */
    private List<String> readStringList(String json) {
        if (json == null || json.isBlank()) {
            return List.of();
        }
        try {
            return List.copyOf(objectMapper.readValue(json, STRING_LIST));
        } catch (JsonProcessingException ignored) {
            return List.of();
        }
    }

    /** 将计划和事实列表序列化为数据库 JSON 文本。 */
    private String writeJson(Object value) {
        try {
            return objectMapper.writeValueAsString(value);
        } catch (JsonProcessingException exception) {
            throw badRequest("工作流计划无法序列化。", exception);
        }
    }

    private static List<String> safeList(List<String> values) {
        return values == null ? List.of() : values.stream()
                .filter(value -> value != null && !value.isBlank())
                .map(String::trim)
                .distinct()
                .toList();
    }

    private static String required(String value, String message) {
        String normalized = trimToNull(value);
        if (normalized == null) {
            throw badRequest(message);
        }
        return normalized;
    }

    private static String defaultText(String value, String fallback) {
        String normalized = trimToNull(value);
        return normalized == null ? fallback : normalized;
    }

    private static String trimToNull(String value) {
        return value == null || value.isBlank() ? null : value.trim();
    }

    private static boolean same(String left, String right) {
        return defaultText(left, "").equalsIgnoreCase(defaultText(right, ""));
    }

    private static ResponseStatusException badRequest(String message) {
        return new ResponseStatusException(HttpStatus.BAD_REQUEST, message);
    }

    private static ResponseStatusException badRequest(String message, Throwable cause) {
        return new ResponseStatusException(HttpStatus.BAD_REQUEST, message, cause);
    }

    private static ResponseStatusException conflict(String message) {
        return new ResponseStatusException(HttpStatus.CONFLICT, message);
    }
}
