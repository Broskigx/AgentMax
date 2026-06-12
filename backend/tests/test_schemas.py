"""Tests for Pydantic schema validation."""

from __future__ import annotations

import uuid

import pytest
from backend.schemas.license import (
    LicenseCreate,
    LicenseRevoke,
    LicenseUpdate,
    PlanCreate,
    TelemetryBatchIn,
    TelemetryEventIn,
)
from pydantic import ValidationError

# ── PlanCreate ────────────────────────────────────────────────────────────────


class TestPlanCreate:
    def test_valid_plan(self) -> None:
        plan = PlanCreate(name="pro", display_name="Pro Plan", max_devices=3)
        assert plan.name == "pro"
        assert plan.is_active is True

    def test_name_pattern_lowercase_only(self) -> None:
        with pytest.raises(ValidationError):
            PlanCreate(name="Pro Plan", display_name="Pro")

    def test_name_pattern_no_spaces(self) -> None:
        with pytest.raises(ValidationError):
            PlanCreate(name="my plan", display_name="My Plan")

    def test_name_allows_underscores_and_numbers(self) -> None:
        plan = PlanCreate(name="pro_v2", display_name="Pro v2")
        assert plan.name == "pro_v2"

    def test_name_min_length(self) -> None:
        with pytest.raises(ValidationError):
            PlanCreate(name="a", display_name="A")

    def test_max_devices_min(self) -> None:
        with pytest.raises(ValidationError):
            PlanCreate(name="pro", display_name="Pro", max_devices=0)

    def test_max_devices_max(self) -> None:
        with pytest.raises(ValidationError):
            PlanCreate(name="pro", display_name="Pro", max_devices=101)

    def test_duration_days_none_means_lifetime(self) -> None:
        plan = PlanCreate(name="lifetime", display_name="Lifetime", duration_days=None)
        assert plan.duration_days is None

    def test_duration_days_must_be_positive(self) -> None:
        with pytest.raises(ValidationError):
            PlanCreate(name="pro", display_name="Pro", duration_days=0)

    def test_metadata_defaults_to_empty_dict(self) -> None:
        plan = PlanCreate(name="pro", display_name="Pro")
        assert plan.metadata == {}


# ── LicenseCreate ─────────────────────────────────────────────────────────────


class TestLicenseCreate:
    def test_valid_license_no_email(self) -> None:
        lic = LicenseCreate(plan_id=uuid.uuid4())
        assert lic.user_email is None

    def test_valid_license_with_email(self) -> None:
        lic = LicenseCreate(
            plan_id=uuid.uuid4(),
            user_email="user@example.com",
            user_name="John Doe",
        )
        assert str(lic.user_email) == "user@example.com"

    def test_invalid_email(self) -> None:
        with pytest.raises(ValidationError):
            LicenseCreate(plan_id=uuid.uuid4(), user_email="not-an-email")

    def test_user_name_max_length(self) -> None:
        with pytest.raises(ValidationError):
            LicenseCreate(plan_id=uuid.uuid4(), user_name="x" * 257)

    def test_metadata_defaults_empty(self) -> None:
        lic = LicenseCreate(plan_id=uuid.uuid4())
        assert lic.metadata == {}


# ── LicenseRevoke ─────────────────────────────────────────────────────────────


class TestLicenseRevoke:
    def test_valid_reason(self) -> None:
        rev = LicenseRevoke(reason="ToS violation")
        assert rev.reason == "ToS violation"

    def test_reason_too_short(self) -> None:
        with pytest.raises(ValidationError):
            LicenseRevoke(reason="ab")

    def test_reason_too_long(self) -> None:
        with pytest.raises(ValidationError):
            LicenseRevoke(reason="x" * 513)

    def test_reason_missing(self) -> None:
        with pytest.raises(ValidationError):
            LicenseRevoke()


# ── LicenseUpdate ─────────────────────────────────────────────────────────────


class TestLicenseUpdate:
    def test_all_none_is_valid(self) -> None:
        upd = LicenseUpdate()
        assert upd.user_email is None
        assert upd.user_name is None

    def test_partial_update(self) -> None:
        upd = LicenseUpdate(user_name="New Name")
        assert upd.user_name == "New Name"
        assert upd.user_email is None

    def test_invalid_email(self) -> None:
        with pytest.raises(ValidationError):
            LicenseUpdate(user_email="bad")


# ── TelemetryEventIn ──────────────────────────────────────────────────────────


class TestTelemetryEventIn:
    def test_valid_event(self) -> None:
        ev = TelemetryEventIn(
            event_name="task.completed",
            client_version="1.2.0",
            platform="win32",
        )
        assert ev.event_name == "task.completed"

    def test_event_name_pattern(self) -> None:
        with pytest.raises(ValidationError):
            TelemetryEventIn(event_name="UPPERCASE_EVENT")

    def test_event_name_must_start_with_letter(self) -> None:
        with pytest.raises(ValidationError):
            TelemetryEventIn(event_name="1.invalid")

    def test_event_name_too_long(self) -> None:
        with pytest.raises(ValidationError):
            TelemetryEventIn(event_name="a" + "." + "b" * 63)

    def test_payload_defaults_empty(self) -> None:
        ev = TelemetryEventIn(event_name="task.completed")
        assert ev.payload == {}


# ── TelemetryBatchIn ──────────────────────────────────────────────────────────


class TestTelemetryBatchIn:
    def test_single_event_batch(self) -> None:
        batch = TelemetryBatchIn(
            events=[
                TelemetryEventIn(event_name="task.started"),
            ]
        )
        assert len(batch.events) == 1

    def test_empty_batch_invalid(self) -> None:
        with pytest.raises(ValidationError):
            TelemetryBatchIn(events=[])

    def test_too_many_events(self) -> None:
        with pytest.raises(ValidationError):
            TelemetryBatchIn(
                events=[TelemetryEventIn(event_name="task.completed") for _ in range(101)]
            )
