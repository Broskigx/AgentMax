"""Shared closed-beta feature profile."""

from __future__ import annotations

import json
import os
from collections.abc import Mapping
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]


def _truthy(value: Any) -> bool:
    return str(value).strip().lower() in {"1", "true", "yes", "on", "si"}


def _boolean(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return value != 0
    return _truthy(value)


def _read_json(path: Path, errors: list[str]) -> dict[str, Any]:
    if not path.exists():
        return {}
    try:
        value = json.loads(path.read_text(encoding="utf-8-sig"))
    except OSError as exc:
        errors.append(f"{path.name}: read failed: {exc}")
        return {}
    except json.JSONDecodeError as exc:
        errors.append(
            f"{path.name}: invalid JSON at line {exc.lineno}, "
            f"column {exc.colno}: {exc.msg}"
        )
        return {}
    if not isinstance(value, dict):
        errors.append(f"{path.name}: expected JSON object")
        return {}
    return value


@dataclass(slots=True)
class FeatureFlags:
    mouse_control: bool = False
    keyboard_control: bool = False
    screen_vision: bool = True
    terminal: bool = False
    file_actions: bool = False
    cloud_agentpilot: bool = False
    local_agentpilot: bool = True
    telemetry: bool = False
    crash_reports: bool = False
    redis_queue: bool = False
    sqlite_storage: bool = True

    @classmethod
    def from_mapping(
        cls,
        data: Mapping[str, Any] | None,
        *,
        environ: Mapping[str, str] | None = None,
    ) -> FeatureFlags:
        values = asdict(cls())
        for key, value in (data or {}).items():
            if key in values:
                values[key] = _boolean(value)
        env = environ if environ is not None else os.environ
        for key in values:
            env_key = f"AGENTMAX_FEATURE_{key.upper()}"
            if env_key in env:
                values[key] = _truthy(env[env_key])
        return cls(**values)

    def to_dict(self) -> dict[str, bool]:
        return asdict(self)


@dataclass(slots=True)
class ConfigProfile:
    data: dict[str, Any]
    feature_flags: FeatureFlags
    errors: list[str]


def load_config_profile(root: Path | None = None) -> ConfigProfile:
    base = root or ROOT
    errors: list[str] = []
    merged: dict[str, Any] = {}
    flags: dict[str, Any] = {}
    for name in ("agentmax.config.json", "beta_config.json"):
        current = _read_json(base / name, errors)
        current_flags = current.pop("feature_flags", {})
        merged.update(current)
        if isinstance(current_flags, dict):
            flags.update(current_flags)
    return ConfigProfile(
        data=merged,
        feature_flags=FeatureFlags.from_mapping(flags),
        errors=errors,
    )


def required_features_for_tool(
    tool_id: str,
    payload: Mapping[str, Any] | None = None,
) -> tuple[str, ...]:
    if tool_id == "computer.execute":
        features: list[str] = []
        actions = (payload or {}).get("actions", [])
        for action in actions if isinstance(actions, list) else []:
            action_type = str((action or {}).get("action") or "").lower()
            if action_type in {"click", "double_click", "right_click", "move", "scroll", "drag"}:
                features.extend(("mouse_control", "screen_vision"))
            elif action_type in {"type", "key", "hotkey", "press"}:
                features.extend(("keyboard_control", "screen_vision"))
            elif action_type == "screenshot":
                features.append("screen_vision")
        return tuple(dict.fromkeys(features))
    if tool_id.startswith("mouse."):
        return ("mouse_control", "screen_vision")
    if tool_id.startswith("keyboard."):
        return ("keyboard_control", "screen_vision")
    if tool_id.startswith(("screen.", "window.")):
        return ("screen_vision",)
    if tool_id.startswith(("app.", "ui.")):
        return ("screen_vision", "mouse_control")
    if tool_id.startswith("shell."):
        return ("terminal",)
    if tool_id.startswith("filesystem."):
        return ("file_actions",)
    return ()


def disabled_features(
    tool_id: str,
    flags: FeatureFlags,
    payload: Mapping[str, Any] | None = None,
) -> list[str]:
    return [
        name
        for name in required_features_for_tool(tool_id, payload)
        if not bool(getattr(flags, name))
    ]


__all__ = [
    "ConfigProfile",
    "FeatureFlags",
    "disabled_features",
    "load_config_profile",
    "required_features_for_tool",
]
