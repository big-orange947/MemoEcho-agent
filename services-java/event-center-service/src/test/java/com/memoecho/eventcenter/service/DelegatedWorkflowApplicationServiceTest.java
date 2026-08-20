package com.memoecho.eventcenter.service;

import com.fasterxml.jackson.databind.ObjectMapper;
import com.memoecho.eventcenter.dto.DelegatedWorkflowResponse;
import com.memoecho.eventcenter.dto.DelegatedWorkflowArtifactRequest;
import com.memoecho.eventcenter.dto.DelegatedWorkflowStepCompleteRequest;
import com.memoecho.eventcenter.model.DelegatedTask;
import com.memoecho.eventcenter.model.DelegatedWorkflow;
import com.memoecho.eventcenter.repository.JdbcDelegatedTaskRepository;
import com.memoecho.eventcenter.repository.JdbcDelegatedWorkflowRepository;
import com.memoecho.eventcenter.repository.JdbcDelegatedWorkflowStepDispatchRepository;
import org.junit.jupiter.api.Test;
import org.mockito.ArgumentCaptor;

import java.time.Instant;
import java.util.List;
import java.util.Map;
import java.util.Optional;

import static org.assertj.core.api.Assertions.assertThat;
import static org.mockito.ArgumentMatchers.any;
import static org.mockito.ArgumentMatchers.contains;
import static org.mockito.ArgumentMatchers.eq;
import static org.mockito.ArgumentMatchers.isNull;
import static org.mockito.Mockito.mock;
import static org.mockito.Mockito.never;
import static org.mockito.Mockito.verify;
import static org.mockito.Mockito.when;

class DelegatedWorkflowApplicationServiceTest {

    private static final String USER_ID = "user-1";
    private static final String WORKFLOW_ID = "workflow-1";
    private static final Instant CREATED_AT = Instant.parse("2026-08-04T00:00:00Z");

    private final JdbcDelegatedWorkflowRepository workflowRepository =
            mock(JdbcDelegatedWorkflowRepository.class);
    private final JdbcDelegatedTaskRepository taskRepository = mock(JdbcDelegatedTaskRepository.class);
    private final JdbcDelegatedWorkflowStepDispatchRepository dispatchRepository =
            mock(JdbcDelegatedWorkflowStepDispatchRepository.class);
    private final DelegatedTaskApplicationService taskApplicationService =
            mock(DelegatedTaskApplicationService.class);
    private final ObjectMapper objectMapper = new ObjectMapper().findAndRegisterModules();
    private final DelegatedWorkflowApplicationService service = new DelegatedWorkflowApplicationService(
            workflowRepository, taskRepository, dispatchRepository, taskApplicationService, objectMapper);

    /**
     * 验证根步骤完成后，共享事实会写回父工作流，并且满足依赖和事实条件的后继步骤会被激活。
     */
    @Test
    void shouldMergeFactsAndActivateReadySuccessor() throws Exception {
        DelegatedWorkflow runningWorkflow = workflow("RUNNING", "{}", "工作流正在执行。", null);
        DelegatedTask activeRoot = task(
                "ask_contact", 1, "ACTIVE", "[]", "[]", "[\"availability\"]");
        DelegatedTask blockedChild = task(
                "notify_contact", 2, "BLOCKED", "[\"ask_contact\"]",
                "[\"availability\"]", "[\"notification_result\"]");
        DelegatedTask completedRoot = withStatus(activeRoot, "COMPLETED");
        DelegatedTask activeChild = withStatus(blockedChild, "ACTIVE");
        DelegatedWorkflow updatedWorkflow = workflow(
                "RUNNING", "{\"availability\":\"晚上七点\"}", "已完成 1/2 个步骤。", null);

        when(workflowRepository.findByIdAndUserIdForUpdate(WORKFLOW_ID, USER_ID))
                .thenReturn(Optional.of(runningWorkflow));
        when(taskRepository.findByWorkflowId(WORKFLOW_ID)).thenReturn(
                List.of(activeRoot, blockedChild),
                List.of(completedRoot, blockedChild),
                List.of(completedRoot, activeChild),
                List.of(completedRoot, activeChild));
        when(taskRepository.completeWorkflowStep(
                eq(WORKFLOW_ID), eq("ask_contact"), eq(USER_ID), any(), any(), any()))
                .thenReturn(1);
        when(taskRepository.activateWorkflowStep(
                eq(WORKFLOW_ID), eq("notify_contact"), eq(USER_ID), any(), any(), any()))
                .thenReturn(1);
        when(workflowRepository.findByIdAndUserId(WORKFLOW_ID, USER_ID))
                .thenReturn(Optional.of(updatedWorkflow));

        DelegatedWorkflowResponse response = service.completeStep(
                USER_ID,
                WORKFLOW_ID,
                "ask_contact",
                new DelegatedWorkflowStepCompleteRequest(
                        null, Map.of("availability", "晚上七点"), "已确认时间", Map.of("accepted", true), null));

        ArgumentCaptor<String> factsCaptor = ArgumentCaptor.forClass(String.class);
        verify(workflowRepository).updateRuntimeState(
                eq(WORKFLOW_ID), eq(USER_ID), eq("RUNNING"), factsCaptor.capture(),
                contains("1/2"), eq(""), any(Instant.class), isNull());
        verify(taskRepository).activateWorkflowStep(
                eq(WORKFLOW_ID), eq("notify_contact"), eq(USER_ID), any(), any(Instant.class), any());
        verify(dispatchRepository).enqueue(
                eq(WORKFLOW_ID), eq("notify_contact"), eq(1L),
                eq("task-notify_contact"), eq(USER_ID), any(Instant.class));
        assertThat(objectMapper.readTree(factsCaptor.getValue()).path("availability").asText())
                .isEqualTo("晚上七点");
        assertThat(response.status()).isEqualTo("RUNNING");
        assertThat(response.steps()).extracting(step -> step.status())
                .containsExactly("COMPLETED", "ACTIVE");
    }

    /**
     * 验证最后一个活动步骤完成后，父工作流会进入 COMPLETED，并记录完成时间。
     */
    @Test
    void shouldCompleteParentWhenEveryStepIsCompleted() {
        DelegatedWorkflow runningWorkflow = workflow("RUNNING", "{}", "工作流正在执行。", null);
        DelegatedTask activeStep = task("finish", 1, "ACTIVE", "[]", "[]", "[\"result\"]");
        DelegatedTask completedStep = withStatus(activeStep, "COMPLETED");
        Instant completedAt = Instant.parse("2026-08-04T01:00:00Z");
        DelegatedWorkflow completedWorkflow = workflow(
                "COMPLETED", "{\"result\":\"done\"}", "工作流全部步骤已完成。", completedAt);

        when(workflowRepository.findByIdAndUserIdForUpdate(WORKFLOW_ID, USER_ID))
                .thenReturn(Optional.of(runningWorkflow));
        when(taskRepository.findByWorkflowId(WORKFLOW_ID)).thenReturn(
                List.of(activeStep), List.of(completedStep), List.of(completedStep), List.of(completedStep));
        when(taskRepository.completeWorkflowStep(
                eq(WORKFLOW_ID), eq("finish"), eq(USER_ID), any(), any(), any()))
                .thenReturn(1);
        when(workflowRepository.findByIdAndUserId(WORKFLOW_ID, USER_ID))
                .thenReturn(Optional.of(completedWorkflow));

        DelegatedWorkflowResponse response = service.completeStep(
                USER_ID,
                WORKFLOW_ID,
                "finish",
                new DelegatedWorkflowStepCompleteRequest(null, Map.of("result", "done"), "完成", "done", null));

        verify(workflowRepository).updateRuntimeState(
                eq(WORKFLOW_ID), eq(USER_ID), eq("COMPLETED"), any(),
                any(), eq(""), any(Instant.class), any(Instant.class));
        assertThat(response.status()).isEqualTo("COMPLETED");
        assertThat(response.completedAt()).isEqualTo(completedAt);
        assertThat(response.steps()).extracting(step -> step.status()).containsExactly("COMPLETED");
    }

    /**
     * 验证 Runtime 重复回调已完成步骤时只读取当前状态，不重复写事实、激活步骤或触发外部动作。
     */
    @Test
    void shouldTreatRepeatedCompletionCallbackAsIdempotent() {
        DelegatedWorkflow workflow = workflow(
                "RUNNING", "{\"availability\":\"晚上七点\"}", "已完成 1/2 个步骤。", null);
        DelegatedTask completedStep = withStatus(
                task("ask_contact", 1, "ACTIVE", "[]", "[]", "[\"availability\"]"),
                "COMPLETED");

        when(workflowRepository.findByIdAndUserIdForUpdate(WORKFLOW_ID, USER_ID))
                .thenReturn(Optional.of(workflow));
        when(taskRepository.findByWorkflowId(WORKFLOW_ID)).thenReturn(List.of(completedStep));

        DelegatedWorkflowResponse response = service.completeStep(
                USER_ID,
                WORKFLOW_ID,
                "ask_contact",
                new DelegatedWorkflowStepCompleteRequest(
                        null, Map.of("availability", "重复值"), "重复回调", Map.of(), null));

        verify(taskRepository, never()).completeWorkflowStep(any(), any(), any(), any(), any(), any());
        verify(taskRepository, never()).activateWorkflowStep(any(), any(), any(), any(), any(), any());
        verify(workflowRepository, never()).updateRuntimeState(
                any(), any(), any(), any(), any(), any(), any(), any());
        assertThat(response.steps()).extracting(step -> step.status()).containsExactly("COMPLETED");
    }

    /**
     * 验证上游步骤通过类型化产物发布事实时，产物名称被合并为共享事实，
     * 类型化产物连同来源事件写入步骤结果，后继步骤以触发事件作为起点水位被激活。
     */
    @Test
    void shouldPublishTypedArtifactAndActivateSuccessorWithWatermark() throws Exception {
        DelegatedWorkflow runningWorkflow = workflow("RUNNING", "{}", "工作流正在执行。", null);
        DelegatedTask activeRoot = task(
                "ask_km", 1, "ACTIVE", "[]", "[]", "[\"class_time\"]");
        DelegatedTask blockedChild = task(
                "relay_xiaohao", 2, "BLOCKED", "[\"ask_km\"]",
                "[\"class_time\"]", "[\"relay_result\"]");
        DelegatedTask completedRoot = withStatus(activeRoot, "COMPLETED");
        DelegatedTask activeChild = withStatus(blockedChild, "ACTIVE");
        DelegatedWorkflow updatedWorkflow = workflow(
                "RUNNING", "{\"class_time\":\"19:30\"}", "已完成 1/2 个步骤。", null);

        when(workflowRepository.findByIdAndUserIdForUpdate(WORKFLOW_ID, USER_ID))
                .thenReturn(Optional.of(runningWorkflow));
        when(taskRepository.findByWorkflowId(WORKFLOW_ID)).thenReturn(
                List.of(activeRoot, blockedChild),
                List.of(completedRoot, blockedChild),
                List.of(completedRoot, activeChild),
                List.of(completedRoot, activeChild));
        when(taskRepository.completeWorkflowStep(
                eq(WORKFLOW_ID), eq("ask_km"), eq(USER_ID), any(), any(), any()))
                .thenReturn(1);
        when(taskRepository.activateWorkflowStep(
                eq(WORKFLOW_ID), eq("relay_xiaohao"), eq(USER_ID), any(), any(), eq("event-km-reply")))
                .thenReturn(1);
        when(workflowRepository.findByIdAndUserId(WORKFLOW_ID, USER_ID))
                .thenReturn(Optional.of(updatedWorkflow));

        DelegatedWorkflowResponse response = service.completeStep(
                USER_ID,
                WORKFLOW_ID,
                "ask_km",
                new DelegatedWorkflowStepCompleteRequest(
                        List.of(new DelegatedWorkflowArtifactRequest(
                                "CLASS_TIME", "class_time", "19:30", "event-km-reply")),
                        Map.of(),
                        "已收到上课时间",
                        Map.of("accepted", true),
                        "event-km-reply"));

        ArgumentCaptor<String> factsCaptor = ArgumentCaptor.forClass(String.class);
        verify(workflowRepository).updateRuntimeState(
                eq(WORKFLOW_ID), eq(USER_ID), eq("RUNNING"), factsCaptor.capture(),
                contains("1/2"), eq(""), any(Instant.class), isNull());
        assertThat(objectMapper.readTree(factsCaptor.getValue()).path("class_time").asText())
                .isEqualTo("19:30");
        // 类型化产物与来源事件必须原子写入步骤结果。
        ArgumentCaptor<String> resultCaptor = ArgumentCaptor.forClass(String.class);
        verify(taskRepository).completeWorkflowStep(
                eq(WORKFLOW_ID), eq("ask_km"), eq(USER_ID), resultCaptor.capture(), any(), any());
        var artifactsNode = objectMapper.readTree(resultCaptor.getValue()).path("artifacts");
        assertThat(artifactsNode).isNotEmpty();
        assertThat(artifactsNode.get(0).path("type").asText()).isEqualTo("CLASS_TIME");
        assertThat(artifactsNode.get(0).path("sourceEventId").asText()).isEqualTo("event-km-reply");
        // 后继步骤以触发事件为起点水位激活。
        verify(taskRepository).activateWorkflowStep(
                eq(WORKFLOW_ID), eq("relay_xiaohao"), eq(USER_ID), any(), any(Instant.class),
                eq("event-km-reply"));
        assertThat(response.steps()).extracting(step -> step.status())
                .containsExactly("COMPLETED", "ACTIVE");
    }

    /**
     * 验证类型化产物必须声明在 producesFacts 中，未声明产物被拒绝而不是静默入库。
     */
    @Test
    void shouldRejectArtifactNotDeclaredInProducesFacts() {
        DelegatedWorkflow runningWorkflow = workflow("RUNNING", "{}", "工作流正在执行。", null);
        DelegatedTask activeRoot = task("ask_km", 1, "ACTIVE", "[]", "[]", "[\"class_time\"]");
        when(workflowRepository.findByIdAndUserIdForUpdate(WORKFLOW_ID, USER_ID))
                .thenReturn(Optional.of(runningWorkflow));
        when(taskRepository.findByWorkflowId(WORKFLOW_ID)).thenReturn(List.of(activeRoot));

        org.junit.jupiter.api.Assertions.assertThrows(
                org.springframework.web.server.ResponseStatusException.class,
                () -> service.completeStep(
                        USER_ID,
                        WORKFLOW_ID,
                        "ask_km",
                        new DelegatedWorkflowStepCompleteRequest(
                                List.of(new DelegatedWorkflowArtifactRequest(
                                        "SECRET", "leaked_value", "不应入库", null)),
                                Map.of(),
                                "错误产物",
                                Map.of(),
                                null)));

        verify(taskRepository, never()).completeWorkflowStep(any(), any(), any(), any(), any(), any());
    }

    /** 创建测试用父工作流，集中维护与状态推进无关的固定字段。 */
    private DelegatedWorkflow workflow(String status, String factsJson, String progress, Instant completedAt) {
        return new DelegatedWorkflow(
                WORKFLOW_ID, USER_ID, "execution-1", "原始命令", "测试工作流", "PLAN_EXECUTE",
                status, "{}", factsJson, progress, "", CREATED_AT, CREATED_AT, completedAt);
    }

    /** 创建测试用步骤，显式保留依赖、输入事实和输出事实，便于阅读 DAG 场景。 */
    private DelegatedTask task(
            String stepKey,
            int order,
            String status,
            String dependsOnJson,
            String requiredFactsJson,
            String producesFactsJson
    ) {
        return new DelegatedTask(
                "task-" + stepKey,
                WORKFLOW_ID,
                stepKey,
                order,
                "ACTION",
                "执行步骤 " + stepKey,
                dependsOnJson,
                requiredFactsJson,
                producesFactsJson,
                "{}",
                0L,
                USER_ID,
                "delegated",
                status,
                "原始命令",
                "execution-1",
                "km",
                "qq",
                "private",
                "10001",
                "km",
                "完成目标",
                "满足成功条件",
                "",
                0.9,
                "",
                false,
                "AUTO_COMPLETE",
                "等待执行",
                "{}",
                "",
                "",
                "",
                CREATED_AT,
                null,
                "",
                CREATED_AT,
                CREATED_AT);
    }

    /** 复制步骤并替换状态，模拟数据库更新后的最新快照。 */
    private DelegatedTask withStatus(DelegatedTask task, String status) {
        return new DelegatedTask(
                task.id(), task.workflowId(), task.stepKey(), task.stepOrder(), task.stepRole(),
                task.stepInstruction(), task.dependsOnJson(), task.requiredFactsJson(),
                task.producesFactsJson(), task.resultJson(), task.activationVersion(), task.userId(),
                task.taskType(), status, task.originalCommand(), task.sourceExecutionId(), task.targetQuery(),
                task.platform(), task.chatType(), task.chatId(), task.targetName(), task.objective(),
                task.successCriteria(), task.deadlineText(), task.confidence(), task.clarificationQuestion(),
                task.requiresConfirmation(), task.executionMode(), task.progressSummary(), task.stateJson(),
                task.lastEventId(), task.startEventId(), task.conversationScopeJson(), task.startedAt(),
                "COMPLETED".equals(status) ? CREATED_AT.plusSeconds(60) : task.completedAt(),
                task.completionReport(), task.createdAt(), CREATED_AT.plusSeconds(60));
    }
}
