from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class PlannerStep:
    tool: str
    reason: str
    required_context: tuple[str, ...] = ()
    optional: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "tool": self.tool,
            "reason": self.reason,
            "required_context": list(self.required_context),
            "optional": self.optional,
        }


@dataclass(frozen=True)
class AgentPlan:
    mode: str
    objective: str
    reason: str
    steps: tuple[PlannerStep, ...]

    def tool_names(self) -> list[str]:
        return [step.tool for step in self.steps]

    def user_steps(self) -> list[str]:
        return [step.reason for step in self.steps]

    def get_step(self, tool_name: str) -> PlannerStep | None:
        return next((step for step in self.steps if step.tool == tool_name), None)

    def to_dict(self) -> dict[str, Any]:
        return {
            "mode": self.mode,
            "objective": self.objective,
            "reason": self.reason,
            "tool_sequence": self.tool_names(),
            "steps": [step.to_dict() for step in self.steps],
        }


class MaritimeAgentPlanner:
    name = "RuleBasedMaritimePlanner"
    version = "planner_step_5_evaluable"

    def __init__(self, max_sql_attempts: int = 2) -> None:
        self.max_sql_attempts = max(1, max_sql_attempts)

    def describe(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "version": self.version,
            "max_sql_attempts": self.max_sql_attempts,
            "strategy": "规则型任务规划：根据路由结果选择澄清链路或查询链路，查询前召回历史记忆，结束后保存问数记忆，并通过离线评测固定关键行为边界。",
        }

    def initial_plan(self) -> AgentPlan:
        return AgentPlan(
            mode="bootstrap",
            objective="建立本次问数任务的上下文和路由判断",
            reason="任何问数任务都需要先读取数据目录与术语库，再识别用户问题是否足够明确。",
            steps=(
                PlannerStep(
                    "context.load",
                    "读取当前数据集、字段和术语，为 Agent 建立上下文。",
                    (),
                ),
                PlannerStep(
                    "intent.route",
                    "根据问题、术语和手动选择结果识别意图与主数据表。",
                    ("datasets", "terms"),
                ),
            ),
        )

    def plan_after_route(self, route_info: dict[str, Any]) -> AgentPlan:
        if route_info.get("answer_status") == "need_clarification":
            return AgentPlan(
                mode="clarify",
                objective="向用户请求缺失的业务上下文",
                reason="路由模块无法确定唯一数据表或业务对象，Agent 不进入 SQL 查询。",
                steps=(
                    PlannerStep(
                        "memory.recall",
                        "召回相似历史问数，判断是否存在可参考的问题和 SQL 经验。",
                        ("question", "route_info"),
                        optional=True,
                    ),
                    PlannerStep(
                        "answer.clarify",
                        "生成补充条件提示，并保留候选数据表和追溯编号。",
                        ("route_info",),
                    ),
                    PlannerStep(
                        "memory.save",
                        "保存本次澄清记录，供后续追溯问题为何未进入 SQL 查询。",
                        ("question", "route_info", "answer_payload"),
                        optional=True,
                    ),
                ),
            )

        return AgentPlan(
            mode="query",
            objective="生成并执行受控 SQL，返回可追溯答案",
            reason="路由结果足够明确，Agent 进入数据查询和答案生成链路。",
            steps=(
                PlannerStep(
                    "memory.recall",
                    "召回相似历史问数，向 SQL 生成提供可参考的业务口径和历史查询形态。",
                    ("question", "route_info"),
                    optional=True,
                ),
                PlannerStep(
                    "table.plan",
                    "选择主表和必要关联表，生成受控 JOIN 上下文。",
                    ("datasets", "terms", "route_info"),
                ),
                PlannerStep(
                    "terms.retrieve",
                    "检索与问题相关的业务术语和统计口径。",
                    ("selected_datasets", "context_terms"),
                ),
                PlannerStep(
                    "codes.resolve",
                    "解析字段编码映射，约束 SQL 只能使用真实数据库编码。",
                    ("selected_datasets", "route_info"),
                ),
                PlannerStep(
                    "model.configure",
                    "校验并读取本次请求使用的模型配置。",
                    ("model_payload",),
                ),
                PlannerStep(
                    "sql.generate",
                    "基于 schema、术语、编码和 JOIN 计划生成只读 SQL。",
                    ("config", "dataset", "matched_terms", "route_info", "code_context"),
                ),
                PlannerStep(
                    "sql.validate",
                    "执行 SQL 安全、语义和编码边界校验。",
                    ("sql", "multi_table_context", "code_context"),
                ),
                PlannerStep(
                    "query.execute",
                    "只读执行 SQL，并将结果编码翻译为业务名称。",
                    ("sql", "code_context"),
                ),
                PlannerStep(
                    "answer.build",
                    "构建追溯记录和结构化答案事实。",
                    ("rows", "route_info", "matched_terms", "sql"),
                ),
                PlannerStep(
                    "answer.summarize",
                    "基于查询事实生成中文回答。",
                    ("answer_payload", "trace_record"),
                ),
                PlannerStep(
                    "memory.save",
                    "保存本次成功问数的路由、SQL、结果摘要和修复状态。",
                    ("question", "route_info", "sql", "trace_record"),
                    optional=True,
                ),
            ),
        )

    def plan_for_sql_failure(self, failed_tool: str, attempts_used: int, error_message: str) -> AgentPlan:
        can_repair = self.should_repair_sql(failed_tool, attempts_used, error_message)
        if not can_repair:
            return AgentPlan(
                mode="fail",
                objective="停止执行并返回错误",
                reason="SQL 已达到最大尝试次数，或失败工具不适合自动修复。",
                steps=(),
            )
        return AgentPlan(
            mode="repair",
            objective="根据错误反馈修复 SQL 并重试查询",
            reason=f"{failed_tool} 返回错误，仍在最大尝试次数内，Planner 决定调用 SQL 修复工具。",
            steps=(
                PlannerStep(
                    "sql.repair",
                    "把失败 SQL 和错误信息反馈给模型，进行最小必要修复。",
                    ("failed_sql", "error_message", "route_info", "code_context"),
                ),
                PlannerStep(
                    "sql.validate",
                    "修复后重新执行安全、语义和编码边界校验。",
                    ("sql",),
                ),
                PlannerStep(
                    "query.execute",
                    "修复后重新只读执行查询。",
                    ("sql",),
                ),
            ),
        )

    def should_repair_sql(self, failed_tool: str, attempts_used: int, error_message: str) -> bool:
        if failed_tool not in {"sql.validate", "query.execute"}:
            return False
        if attempts_used >= self.max_sql_attempts:
            return False
        # Security validation still blocks execution first; repair is allowed
        # only because the repaired SQL must pass the same whitelist checks.
        return bool(error_message.strip())


DEFAULT_PLANNER = MaritimeAgentPlanner()
