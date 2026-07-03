"""Privacy guards for beta data collection: opt-in only + redaction."""

from __future__ import annotations

import pathlib

from core.data_collection import consent, uploader
from core.data_collection.redactor import redact_record, redact_text


def test_collection_is_disabled_by_default(tmp_path, monkeypatch):
    monkeypatch.delenv("AGENTMAX_BETA_DATA_OPTIN", raising=False)
    monkeypatch.setattr(consent, "_CONSENT_FILE", tmp_path / ".consent.json")
    consent.reset()
    assert consent.is_enabled() is False


def test_env_flag_can_enable_and_disable(tmp_path, monkeypatch):
    monkeypatch.setattr(consent, "_CONSENT_FILE", tmp_path / ".consent.json")
    monkeypatch.setenv("AGENTMAX_BETA_DATA_OPTIN", "1")
    consent.reset()
    assert consent.is_enabled() is True
    monkeypatch.setenv("AGENTMAX_BETA_DATA_OPTIN", "0")
    consent.reset()
    assert consent.is_enabled() is False


def test_recorded_opt_in_persists(tmp_path, monkeypatch):
    cf = tmp_path / ".consent.json"
    monkeypatch.setattr(consent, "_CONSENT_FILE", cf)
    monkeypatch.delenv("AGENTMAX_BETA_DATA_OPTIN", raising=False)
    consent.reset()
    consent.record_consent(True, tester_id="tester-1")
    consent.reset()  # force re-read from disk
    assert consent.is_enabled() is True
    assert cf.exists()


def test_uploader_no_ops_without_consent(tmp_path, monkeypatch):
    # Even fully configured, nothing uploads unless the tester opted in.
    monkeypatch.setattr(consent, "_CONSENT_FILE", tmp_path / ".consent.json")
    monkeypatch.delenv("AGENTMAX_BETA_DATA_OPTIN", raising=False)
    monkeypatch.setenv("AGENTMAX_BETA_DATA_REPO", "owner/repo")
    monkeypatch.setenv("AGENTMAX_BETA_UPLOAD_TOKEN", "ghp_dummy")
    consent.reset()
    (tmp_path / "x.jsonl").write_text('{"a":1}\n', encoding="utf-8")
    assert uploader.sync(pathlib.Path(tmp_path), require_consent=True) == 0


def test_redactor_strips_pii():
    raw = "email a@b.com token=abcdef123456 ip 192.168.0.5 path C:\\Users\\agustin\\x"
    out = redact_text(raw)
    assert "a@b.com" not in out
    assert "abcdef123456" not in out
    assert "192.168.0.5" not in out
    assert "agustin" not in out


def test_redactor_drops_sensitive_fields():
    rec = redact_record(
        {"raw_response": "secret stuff", "image_data": "b64...", "outcome": "solved"}
    )
    assert rec["raw_response"] == "<SECRET>"
    assert rec["image_data"] == "<SECRET>"
    assert rec["outcome"] == "solved"
