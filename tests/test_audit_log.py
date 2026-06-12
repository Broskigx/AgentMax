"""Tests for AuditLog HMAC signing and tamper detection."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest

from core.security.audit_log import AuditLog


class TestAuditLog:
    @pytest.mark.asyncio
    async def test_log_writes_entry(self, tmp_path: Path):
        log_path = tmp_path / "audit.log"
        audit = AuditLog(log_path)
        await audit.start()
        await audit.log_event("test.event", {"key": "value"})
        await asyncio.sleep(0.1)
        await audit.stop()

        lines = log_path.read_text().strip().split("\n")
        assert len(lines) == 1
        entry = json.loads(lines[0])
        assert entry["type"] == "test.event"
        assert entry["key"] == "value"

    @pytest.mark.asyncio
    async def test_hmac_signature_added(self, tmp_path: Path):
        log_path = tmp_path / "audit.log"
        audit = AuditLog(log_path, hmac_key="test-secret-key")
        await audit.start()
        await audit.log_event("signed.event", {"data": 42})
        await asyncio.sleep(0.1)
        await audit.stop()

        lines = log_path.read_text().strip().split("\n")
        entry = json.loads(lines[0])
        assert "sig" in entry, "HMAC signature should be present"
        assert len(entry["sig"]) == 64  # SHA256 hex = 64 chars

    @pytest.mark.asyncio
    async def test_verify_clean_log_passes(self, tmp_path: Path):
        log_path = tmp_path / "audit.log"
        audit = AuditLog(log_path, hmac_key="test-secret-key")
        await audit.start()
        await audit.log_event("ev1", {"x": 1})
        await audit.log_event("ev2", {"x": 2})
        await asyncio.sleep(0.1)
        await audit.stop()

        failures = audit.verify_log()
        assert failures == [], f"Expected no failures, got: {failures}"

    @pytest.mark.asyncio
    async def test_verify_detects_tampered_entry(self, tmp_path: Path):
        log_path = tmp_path / "audit.log"
        audit = AuditLog(log_path, hmac_key="test-secret-key")
        await audit.start()
        await audit.log_event("original", {"amount": 100})
        await asyncio.sleep(0.1)
        await audit.stop()

        # Tamper: change the amount
        lines = log_path.read_text().strip().split("\n")
        entry = json.loads(lines[0])
        entry["amount"] = 99999  # attacker modifies the value
        log_path.write_text(json.dumps(entry) + "\n")

        failures = audit.verify_log()
        assert len(failures) == 1, "Tampered entry should be detected"

    @pytest.mark.asyncio
    async def test_no_signature_without_key(self, tmp_path: Path):
        log_path = tmp_path / "audit.log"
        audit = AuditLog(log_path, hmac_key="")
        await audit.start()
        await audit.log_event("unsigned", {"data": "x"})
        await asyncio.sleep(0.1)
        await audit.stop()

        lines = log_path.read_text().strip().split("\n")
        entry = json.loads(lines[0])
        assert "sig" not in entry

    @pytest.mark.asyncio
    async def test_multiple_events_appended(self, tmp_path: Path):
        log_path = tmp_path / "audit.log"
        audit = AuditLog(log_path)
        await audit.start()
        for i in range(5):
            await audit.log_event(f"ev{i}", {"i": i})
        await asyncio.sleep(0.2)
        await audit.stop()

        lines = [l for l in log_path.read_text().strip().split("\n") if l]
        assert len(lines) == 5
