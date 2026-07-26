from app.schemas.tasks import ExecutionPlan, PlanStep


class PlannerService:
    def build_plan(self, route: str) -> ExecutionPlan:
        # planner 只负责根据 route 生成执行步骤，不直接处理业务。
        if route == "message_dispatch":
            return ExecutionPlan(
                mode="single_agent",
                steps=[PlanStep(agent="inbox_dispatch", action="dispatch_message")],
            )
        if route == "file_analysis":
            return self._with_dispatch(
                ExecutionPlan(
                    mode="multi_agent",
                    steps=[
                        PlanStep(agent="file", action="analyze_attachments"),
                        PlanStep(agent="work", action="build_work_plan"),
                    ],
                )
            )
        if route == "schedule_extract":
            return self._with_dispatch(
                ExecutionPlan(
                    mode="single_agent",
                    steps=[PlanStep(agent="schedule", action="extract_schedule")],
                )
            )
        if route == "task_plan":
            return self._with_dispatch(
                ExecutionPlan(
                    mode="single_agent",
                    steps=[PlanStep(agent="work", action="build_work_plan")],
                )
            )
        if route == "social_reply":
            return self._with_dispatch(
                ExecutionPlan(
                    mode="single_agent",
                    steps=[
                        PlanStep(agent="social", action="draft_reply"),
                        PlanStep(agent="context_review", action="review_context"),
                        PlanStep(agent="review", action="review_reply"),
                    ],
                )
            )
        if route == "group_ops":
            return self._with_dispatch(
                ExecutionPlan(
                    mode="single_agent",
                    steps=[PlanStep(agent="groupops", action="handle_group_ops")],
                )
            )
        return self._with_dispatch(
            ExecutionPlan(
                mode="single_agent",
                steps=[PlanStep(agent="inbox", action="summarize_recent")],
            )
        )

    @staticmethod
    def _with_dispatch(plan: ExecutionPlan) -> ExecutionPlan:
        # 除了普通群消息直分发，其余场景都先经过 inbox_dispatch，
        # 这样后面可以统一挂接快慢通道和优先级判断。
        return ExecutionPlan(
            mode=plan.mode,
            steps=[PlanStep(agent="inbox_dispatch", action="dispatch_message"), *plan.steps],
        )
