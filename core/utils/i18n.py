"""Internationalization (i18n) manager for AgentMax."""

from __future__ import annotations

import structlog

log = structlog.get_logger(__name__)


class I18nManager:
    _instance: I18nManager | None = None

    def __new__(cls) -> I18nManager:
        if cls._instance is None:
            cls._instance = super().__new__(cls)
            cls._instance._initialized = False
        return cls._instance

    def __init__(self) -> None:
        if getattr(self, "_initialized", False):
            return
        self._current_locale = "en"
        self._translations: dict[str, dict[str, str]] = {
            "en": {
                "ipc.shutdown_requested": "Shutdown initiated",
                "ipc.supervisor_not_ready": "Supervisor not ready",
                "ipc.task_not_found": "Task not found",
                "ipc.recording_already_started": "Already recording",
                "ipc.not_recording": "Not recording",
                "ipc.stop_recording_first": "Stop recording first",
                "ipc.license_manager_not_init": "LicenseManager not initialised",
            },
            "es": {
                "ipc.shutdown_requested": "Apagado iniciado",
                "ipc.supervisor_not_ready": "Supervisor no está listo",
                "ipc.task_not_found": "Tarea no encontrada",
                "ipc.recording_already_started": "Ya se está grabando",
                "ipc.not_recording": "No se está grabando",
                "ipc.stop_recording_first": "Detenga la grabación primero",
                "ipc.license_manager_not_init": "LicenseManager no inicializado",
            },
        }
        self._initialized = True

    def set_locale(self, locale: str) -> None:
        if locale in self._translations:
            self._current_locale = locale
            log.info("i18n.locale_changed", locale=locale)
        else:
            log.warning("i18n.locale_not_found", locale=locale)

    def t(self, key: str, default: str | None = None) -> str:
        """Translate a key to the current locale."""
        return self._translations.get(self._current_locale, {}).get(key, default or key)


# Global helper
_manager = I18nManager()


def t(key: str, default: str | None = None) -> str:
    return _manager.t(key, default)


def set_locale(locale: str) -> None:
    _manager.set_locale(locale)
