"""
ConfigManager — loads servermax config and distributes it to clients.

Clients hit GET /config to receive the current server configuration,
including feature flags and available AI backends.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

_DEFAULT_CONFIG_PATH = Path(__file__).parent.parent / "config" / "default.json"


class ConfigManager:
    def __init__(self, config_path: Path = _DEFAULT_CONFIG_PATH) -> None:
        self._path = config_path
        self._config: dict[str, Any] = {}

    def load(self) -> None:
        if self._path.exists():
            with self._path.open(encoding="utf-8") as f:
                self._config = json.load(f)
        else:
            self._config = _DEFAULT_CONFIG

    def get(self) -> dict[str, Any]:
        return self._config.copy()

    def patch(self, patch: dict[str, Any]) -> None:
        self._config.update(patch)


_DEFAULT_CONFIG: dict[str, Any] = {
    "version": "0.1.1",
    "channel": "beta",
    "features": {
        "chat": True,
        "diagnostics": True,
        "feedback": True,
        "sqlite_storage": True,
        "screen_vision": True,
        "mouse_control": False,
        "keyboard_control": False,
        "terminal_runner": False,
        "file_actions": False,
        "cloud_agent_pilot": False,
        "telemetry": False,
        "goal_engine": False,
        "goal_engine_require_approval": True,
        "autolearn": True,
    },
    "ai_backends": [
        {"type": "claude",       "name": "claude",   "model": "claude-sonnet-4-6"},
        {"type": "openai_compat","name": "lmstudio", "url": "http://localhost:1234", "model": ""},
    ],
    "update": {
        "min_version": "0.1.0",
        "latest_version": "0.1.1",
        "download_url": "",
    },
    "servermax": {
        "heartbeat_interval_s": 30,
        "lesson_upload_interval_s": 300,
    },
}
