"""In-memory task plan registry for Milestone 4B."""

from __future__ import annotations

from typing import Dict, Optional

from agent.task_plan import TaskPlan


class TaskPlanRegistry:
    def __init__(self) -> None:
        self._plans: Dict[str, TaskPlan] = {}
        self._latest_id: Optional[str] = None

    def put(self, plan: TaskPlan) -> TaskPlan:
        self._plans[plan.plan_id] = plan
        self._latest_id = plan.plan_id
        return plan

    def get(self, plan_id: str) -> Optional[TaskPlan]:
        return self._plans.get(plan_id)

    def latest(self) -> Optional[TaskPlan]:
        if self._latest_id:
            return self._plans.get(self._latest_id)
        return None

    def clear(self) -> None:
        self._plans.clear()
        self._latest_id = None


_registry: Optional[TaskPlanRegistry] = None


def get_task_plan_registry() -> TaskPlanRegistry:
    global _registry
    if _registry is None:
        _registry = TaskPlanRegistry()
    return _registry


def reset_task_plan_registry() -> None:
    global _registry
    if _registry is not None:
        _registry.clear()
    _registry = TaskPlanRegistry()
