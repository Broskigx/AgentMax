"""Tests for the automation engine: cron matching, registry, workflows, scheduler."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from core.automation.automation_engine import (
    ActionType,
    Automation,
    AutomationEngine,
    AutomationStep,
    AutomationType,
    ExecutionStatus,
    TriggerType,
    cron_matches,
)


def _make_automation(
    automation_id: str = "wf1",
    *,
    trigger_type: TriggerType = TriggerType.MANUAL,
    schedule: str | None = None,
    message: str = "hello",
) -> Automation:
    now = datetime.now(UTC)
    return Automation(
        automation_id=automation_id,
        name="Test Automation",
        description=None,
        automation_type=AutomationType.WORKFLOW,
        trigger_type=trigger_type,
        created_by="test",
        created_at=now,
        updated_at=now,
        steps=[
            AutomationStep(
                step_id="s1",
                action_type=ActionType.LOG,
                config={"message": message},
            )
        ],
        schedule=schedule,
    )


class TestCronMatches:
    def test_wildcard_matches_any_minute(self):
        assert cron_matches("* * * * *", datetime(2026, 6, 16, 4, 30, tzinfo=UTC))

    def test_exact_minute_hour(self):
        when = datetime(2026, 6, 16, 4, 30, tzinfo=UTC)
        assert cron_matches("30 4 * * *", when)
        assert not cron_matches("31 4 * * *", when)

    def test_step_values(self):
        assert cron_matches("*/15 * * * *", datetime(2026, 6, 16, 4, 30, tzinfo=UTC))
        assert not cron_matches("*/15 * * * *", datetime(2026, 6, 16, 4, 31, tzinfo=UTC))

    def test_ranges_and_weekday(self):
        # Tuesday 2026-06-16, 09:00 within Mon-Fri 9-17
        assert cron_matches("0 9-17 * * 1-5", datetime(2026, 6, 16, 9, 0, tzinfo=UTC))
        # Sunday 2026-06-14 is outside Mon-Fri
        assert not cron_matches("0 9-17 * * 1-5", datetime(2026, 6, 14, 9, 0, tzinfo=UTC))

    def test_sunday_accepts_zero_and_seven(self):
        sunday = datetime(2026, 6, 14, 0, 0, tzinfo=UTC)
        assert cron_matches("0 0 * * 0", sunday)
        assert cron_matches("0 0 * * 7", sunday)

    def test_lists(self):
        assert cron_matches("5,10,15 * * * *", datetime(2026, 6, 16, 4, 10, tzinfo=UTC))
        assert not cron_matches("5,10,15 * * * *", datetime(2026, 6, 16, 4, 11, tzinfo=UTC))

    def test_malformed_expression_is_not_due(self):
        assert not cron_matches("not a cron", datetime.now(UTC))
        assert not cron_matches("* * *", datetime.now(UTC))


class TestRegistry:
    def test_register_get_list_unregister(self):
        eng = AutomationEngine()
        auto = _make_automation()
        eng.register_automation(auto)
        assert eng.get_automation("wf1") is auto
        assert [a.automation_id for a in eng.list_automations()] == ["wf1"]
        assert eng.unregister_automation("wf1") is True
        assert eng.unregister_automation("wf1") is False
        assert eng.get_automation("wf1") is None


class TestExecuteWorkflow:
    @pytest.mark.asyncio
    async def test_executes_registered_workflow(self):
        eng = AutomationEngine()
        eng.register_automation(_make_automation())
        result = await eng.execute_workflow("wf1", {"name": "world"})
        assert result.status == ExecutionStatus.COMPLETED
        assert result.step_results[0]["success"] is True

    @pytest.mark.asyncio
    async def test_missing_workflow_raises_keyerror(self):
        eng = AutomationEngine()
        with pytest.raises(KeyError):
            await eng.execute_workflow("does-not-exist", {})

    @pytest.mark.asyncio
    async def test_input_data_available_as_variables(self):
        eng = AutomationEngine()
        eng.register_automation(
            _make_automation(message="hi {{name}} / {{trigger.name}}")
        )
        result = await eng.execute_workflow("wf1", {"name": "world"})
        assert result.step_results[0]["result"]["message"] == "hi world / world"


class TestScheduler:
    @pytest.mark.asyncio
    async def test_due_schedule_fires_once_per_minute(self):
        eng = AutomationEngine()
        now = datetime.now(UTC)
        eng.register_automation(
            _make_automation(
                "sch1",
                trigger_type=TriggerType.SCHEDULE,
                schedule=f"{now.minute} {now.hour} * * *",
            )
        )
        assert await eng._run_due_schedules(now) == 1
        # Same minute → deduplicated, no second fire
        assert await eng._run_due_schedules(now) == 0

    @pytest.mark.asyncio
    async def test_inactive_or_non_matching_not_fired(self):
        eng = AutomationEngine()
        now = datetime.now(UTC)
        # Non-matching schedule (different minute)
        other_minute = (now.minute + 1) % 60
        eng.register_automation(
            _make_automation(
                "sch_nomatch",
                trigger_type=TriggerType.SCHEDULE,
                schedule=f"{other_minute} {now.hour} * * *",
            )
        )
        # Inactive automation that would otherwise match
        inactive = _make_automation(
            "sch_inactive",
            trigger_type=TriggerType.SCHEDULE,
            schedule=f"{now.minute} {now.hour} * * *",
        )
        inactive.is_active = False
        eng.register_automation(inactive)

        assert await eng._run_due_schedules(now) == 0
