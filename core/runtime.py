"""AgentMax runtime - boots all subsystems, manages lifecycle."""

from __future__ import annotations

import asyncio
import signal
import sys
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from typing import TYPE_CHECKING, Any

import structlog

from core.config import AgentMaxConfig, get_config
from core.event_bus import Event, EventBus, get_bus

if TYPE_CHECKING:
    from core.agents.base_agent import AgentContext, BaseAgent

log = structlog.get_logger(__name__)


class AgentMaxRuntime:
    """
    Top-level runtime orchestrator.

    Boot order:
      1. Setup logging
      2. License manager
      3. Token manager
      4. Event bus
      5. Security layer
      6. Memory subsystem
      7. Vision subsystem
      8. Agent pool
      9. IPC server (WebSocket + REST)
     10. Telemetry
     11. Updater
     12. Signal handlers
    """

    def __init__(self, config: AgentMaxConfig | None = None) -> None:
        self.config = config or get_config()
        self.bus: EventBus = get_bus()
        self._subsystems: list[str] = []
        self._agent_pool: dict[str, BaseAgent] = {}
        self._shutdown_event = asyncio.Event()
        self.shutdown_event = self._shutdown_event  # public alias for IPC
        self.license_manager: object | None = None
        self.token_manager: object | None = None
        # Idempotency flag so the bus subscriber to system.shutdown doesn't
        # re-enter shutdown() when shutdown() itself publishes the event.
        self._shutting_down: bool = False
        self._loop: asyncio.AbstractEventLoop | None = None

    # --------------------------------------------------------------------------
    # Boot
    # --------------------------------------------------------------------------

    async def start(self) -> None:
        log.info("runtime.booting", version="0.1.0")
        self._loop = asyncio.get_running_loop()
        await self._setup_logging()
        await self._start_license()
        await self._start_token_manager()
        await self._start_event_bus()

        # Subscribe to shutdown signal. Must be a proper async handler so the
        # bus can `create_task(handler(event))` without "expected coroutine,
        # got Task". The lambda form returned the Task object directly which
        # produced `event_bus.dispatch_error` storms during teardown.
        async def _on_shutdown_event(_evt: Event) -> None:
            await self.shutdown()

        self.bus.subscribe("system.shutdown", _on_shutdown_event)

        await self._start_security()
        await self._start_memory()
        await self._start_vision()
        await self._start_agents()
        await self._start_ipc()
        await self._start_telemetry()
        await self._start_updater()
        self._wire_AgentMax_observers()
        self._install_signal_handlers()
        log.info("runtime.ready", subsystems=self._subsystems)
        await self.bus.publish(Event("system.ready", payload=None, priority=0))

    async def _start_license(self) -> None:
        """Entitlements are disabled until subscriptions return."""
        self.license_manager = None
        self._subsystems.append("entitlements-disabled")
        log.info("runtime.entitlements_disabled")

    async def _start_token_manager(self) -> None:
        """Boot the centralized token/credit system."""
        from core.security.token_manager import create_token_manager

        self.token_manager = create_token_manager(self.config.tokens)

        # Wire audit hook if audit log is already started (security boots later
        # too — this is best-effort; if audit isn't ready yet, we re-wire below).
        if getattr(self, "audit", None) is not None:
            self.token_manager.set_audit_hook(
                lambda evt: self.audit.log_event(evt.get("event", "token.event"), evt)  # type: ignore[arg-type]
            )

        if self.token_manager.is_unlimited:
            self._subsystems.append("tokens:unlimited")
        else:
            d = self.config.tokens.daily_cap or "inf"
            m = getattr(self.config.tokens, "monthly_cap", 0) or "inf"
            self._subsystems.append(f"tokens:daily={d},monthly={m}")

    async def _license_revoked_shutdown(self, reason: str) -> None:
        log.error("entitlements.shutdown_triggered", reason=reason)
        await self.bus.publish(Event("entitlements.revoked", {"reason": reason}))
        await asyncio.sleep(1)
        await self.shutdown()

    def _on_tamper_detected(self) -> None:
        """Called when tampering is detected. Shuts down cleanly.

        May be invoked from the anti-tamper watchdog thread, which has no
        running event loop — schedule the shutdown on the runtime's loop in a
        thread-safe way instead of asyncio.create_task() (which would raise
        "no running event loop").
        """
        log.error("anti_tamper.SHUTDOWN_REQUESTED")
        loop = self._loop
        if loop is not None and loop.is_running():
            asyncio.run_coroutine_threadsafe(self.shutdown(), loop)
        else:
            log.error("anti_tamper.no_running_loop_cannot_shutdown")

    async def _setup_logging(self) -> None:
        import structlog

        structlog.configure(
            processors=[
                structlog.contextvars.merge_contextvars,
                structlog.processors.add_log_level,
                structlog.processors.TimeStamper(fmt="iso"),
                structlog.dev.ConsoleRenderer()
                if self.config.debug
                else structlog.processors.JSONRenderer(),
            ],
            wrapper_class=structlog.make_filtering_bound_logger(10 if self.config.debug else 20),
        )

    async def _start_event_bus(self) -> None:
        await self.bus.start()
        self._subsystems.append("event_bus")

    async def _start_security(self) -> None:
        from core.security.audit_log import AuditLog
        from core.security.permission_manager import PermissionManager

        self.security = PermissionManager(self.config.security)
        hmac_key = getattr(self.config.security, "audit_hmac_key", "")
        self.audit = AuditLog(self.config.security.audit_log, hmac_key=hmac_key)
        await self.audit.start()
        self._subsystems.append("security")

    async def _start_memory(self) -> None:
        from core.memory.long_term import LongTermMemory
        from core.memory.short_term import ShortTermMemory
        from core.memory.visual_memory import VisualMemory

        self.stm = ShortTermMemory(self.config.memory)
        self.ltm = LongTermMemory(self.config.memory)
        self.visual_memory = VisualMemory(self.config.memory)
        await self.ltm.start()
        self._subsystems.append("memory")

    async def _start_vision(self) -> None:
        from core.rust_vision_bridge import RustVisionBridge

        self.capture = RustVisionBridge()
        self.ocr = self.capture
        self.accessibility = self.capture
        await self.capture.start()
        self._subsystems.append("rust_vision_bridge")

    async def _start_agents(self) -> None:
        from core.agents.file_system_agent import FileSystemAgent
        from core.agents.lean_vision_agent import LeanVisionAgent
        from core.agents.memory_agent import MemoryAgent
        from core.agents.planning_agent import PlanningAgent
        from core.agents.security_agent import SecurityAgent
        from core.agents.supervisor import SupervisorAgent
        from core.agents.ui_automation_agent import UIAutomationAgent
        from core.agents.validation_agent import ValidationAgent
        from core.agents.web_search_agent import WebSearchAgent
        from core.agents.workflow_agent import WorkflowAgent

        ctx = self._build_agent_context()

        agents = [
            SecurityAgent(ctx),
            MemoryAgent(ctx),
            LeanVisionAgent(ctx),
            FileSystemAgent(ctx),
            WebSearchAgent(ctx),
            UIAutomationAgent(ctx),
            PlanningAgent(ctx),
            ValidationAgent(ctx),
            WorkflowAgent(ctx),
            SupervisorAgent(ctx),
        ]

        for agent in agents:
            await agent.start()
            self._agent_pool[agent.name] = agent

        self._subsystems.append("agents")

    def _build_agent_context(self) -> AgentContext:
        from core.agents.base_agent import AgentContext

        return AgentContext(
            config=self.config,
            bus=self.bus,
            capture=self.capture,
            ocr=self.ocr,
            accessibility=self.accessibility,
            stm=self.stm,
            ltm=self.ltm,
            visual_memory=self.visual_memory,
            security=self.security,
            audit=self.audit,
            runtime=self,
        )

    async def _start_ipc(self) -> None:
        from core.ipc import IPCServer

        self.ipc = IPCServer(self.config, self.bus, self._agent_pool, runtime=self)
        self._ipc_task = asyncio.create_task(self.ipc.serve(), name="ipc-server")
        self._subsystems.append("ipc")

    async def _start_telemetry(self) -> None:
        from core.telemetry.telemetry_client import TelemetryClient

        self.telemetry = TelemetryClient(
            enabled=getattr(self.config, "telemetry_enabled", True),
            client_version=getattr(self.config, "version", "1.0.0"),
        )
        # Telemetry sends via the license manager's HTTP client when available.
        # When license_manager is None (dev mode / entitlements disabled),
        # telemetry is instantiated but not started -- events are silently discarded.
        if self.license_manager is not None:
            http_client = self.license_manager.get_http_client()  # type: ignore[attr-defined]
            if http_client:
                await self.telemetry.start(http_client)
        self._subsystems.append("telemetry")

    async def _start_updater(self) -> None:
        from core.updater.update_manager import UpdateManager

        server_pubkey = ""
        if self.license_manager is not None:
            server_pubkey = self.license_manager.get_server_public_key() or ""  # type: ignore[attr-defined]
        self.updater = UpdateManager(
            channel=getattr(self.config, "update_channel", "stable"),
            server_public_key=server_pubkey,
        )
        if self.license_manager is not None:
            http_client = self.license_manager.get_http_client()  # type: ignore[attr-defined]
            if http_client:
                await self.updater.start(http_client)
        self._subsystems.append("updater")

    # --------------------------------------------------------------------------
    # Shutdown
    # --------------------------------------------------------------------------

    async def shutdown(self) -> None:
        # Idempotent: every entry point (IPC /api/shutdown, signal handler,
        # bus event, run_daemon finally) eventually lands here. Without this
        # guard, the system.shutdown publish below re-triggered shutdown()
        # via the bus subscriber and produced a tear-down loop.
        if self._shutting_down:
            return
        self._shutting_down = True

        log.info("runtime.shutting_down")
        await self.bus.publish(Event("system.shutdown", payload=None, priority=0))
        if hasattr(self, "telemetry") and self.telemetry:
            await self.telemetry.stop()
        if hasattr(self, "ipc") and self.ipc:
            await self.ipc.stop()
        if hasattr(self, "_ipc_task") and self._ipc_task:
            self._ipc_task.cancel()
            try:
                await self._ipc_task
            except asyncio.CancelledError:
                pass
        for agent in self._agent_pool.values():
            await agent.stop()

        # Safe shutdown: only stop initialized subsystems
        if hasattr(self, "capture") and self.capture:
            await self.capture.stop()
        if hasattr(self, "ltm") and self.ltm:
            await self.ltm.stop()
        if hasattr(self, "audit") and self.audit:
            await self.audit.stop()
        if hasattr(self, "bus") and self.bus:
            await self.bus.stop()

        self._shutdown_event.set()
        log.info("runtime.stopped")

    async def wait_for_shutdown(self) -> None:
        await self._shutdown_event.wait()

    def _wire_AgentMax_observers(self) -> None:
        """
        Subscribe to AgentMax (LMStudio) events so:
          - `ai.thinking` is recorded into the audit log (for trace)
          - `ai.tokens`  is counted against the user's token budget
          - `ai.response` is forwarded to telemetry (best-effort)

        Wiring goes through the bus so the LMStudio client stays decoupled
        from the runtime and the token manager.

        Gated by ``AGENTMAX_OBSERVABILITY`` (env or config).
        Default: **ON**. Set the env var to 0/false/no/off to disable.
        """
        import os

        env_raw = (os.environ.get("AGENTMAX_OBSERVABILITY") or "").strip().lower()
        if env_raw in {"0", "false", "no", "off"}:
            enabled = False
        elif env_raw in {"1", "true", "yes", "on"}:
            enabled = True
        else:
            # Fall back to config field (which itself defaults to True).
            enabled = bool(getattr(self.config, "AgentMax_observability", True))

        if not enabled:
            log.info(
                "runtime.AgentMax_observers_disabled",
                hint="set AGENTMAX_OBSERVABILITY=1 to enable",
            )
            return

        bus = self.bus
        audit = getattr(self, "audit", None)
        token_manager = getattr(self, "token_manager", None)
        telemetry = getattr(self, "telemetry", None)

        async def _on_thinking(evt: Event) -> None:
            payload = evt.payload or {}
            if audit is not None:
                try:
                    audit.log_event(
                        "AgentMax.thinking",
                        {
                            "model": payload.get("model"),
                            "chars": payload.get("chars", 0),
                            # NOTE: el texto completo NO va al audit log por defecto;
                            # solo metadatos. Para guardar el thinking completo,
                            # suscribir un handler dedicado a `ai.thinking`.
                        },
                    )
                except Exception as exc:  # noqa: BLE001
                    log.debug("audit.thinking_log_failed", error=str(exc))

        async def _on_tokens(evt: Event) -> None:
            payload = evt.payload or {}
            total = int(payload.get("total_tokens") or 0)
            if total <= 0 or token_manager is None:
                return
            try:
                token_manager.consume(
                    plan="AgentMax",
                    user="local",
                    delta=total,
                    requests=1,
                    skip_check=True,  # local model: no cap-block, solo contar
                )
            except Exception as exc:  # noqa: BLE001
                log.debug("token_manager.consume_failed", error=str(exc))

        async def _on_response(evt: Event) -> None:
            if telemetry is None:
                return
            try:
                if hasattr(telemetry, "record_event"):
                    telemetry.record_event("ai.response", evt.payload or {})
            except Exception as exc:  # noqa: BLE001
                log.debug("telemetry.ai_response_failed", error=str(exc))

        bus.subscribe("ai.thinking", _on_thinking)
        bus.subscribe("ai.tokens", _on_tokens)
        bus.subscribe("ai.response", _on_response)
        log.info(
            "runtime.AgentMax_observers_wired", topics=["ai.thinking", "ai.tokens", "ai.response"]
        )

    def _install_signal_handlers(self) -> None:
        loop = asyncio.get_running_loop()

        # Use a named shutdown callback so each lambda captures `self`, not `sig`.
        def _shutdown_cb() -> None:
            asyncio.ensure_future(self.shutdown())

        def _shutdown_threadsafe(_signum: int, _frame: object) -> None:
            asyncio.run_coroutine_threadsafe(self.shutdown(), loop)

        if sys.platform == "win32":
            # ProactorEventLoop supports add_signal_handler for SIGINT/SIGTERM.
            # SIGBREAK (Ctrl+Break) is Windows-only and also triggers shutdown.
            for sig in (signal.SIGINT, signal.SIGTERM, signal.SIGBREAK):
                try:
                    loop.add_signal_handler(sig, _shutdown_cb)
                except (NotImplementedError, OSError):
                    # Fallback for edge cases (e.g. running inside a subprocess)
                    signal.signal(sig, _shutdown_threadsafe)
        else:
            for sig in (signal.SIGINT, signal.SIGTERM):
                loop.add_signal_handler(sig, _shutdown_cb)

    # --------------------------------------------------------------------------
    # Task dispatch (called from IPC / UI)
    # --------------------------------------------------------------------------

    def get_agent(self, name: str) -> Any | None:
        """Access the internal agent pool by agent name."""
        return self._agent_pool.get(name)

    async def dispatch_task(self, task_description: str, options: dict | None = None) -> str:
        """Entry point â€" receives a task from the UI and hands it to the Supervisor."""
        from core.agents.supervisor import TaskRequest

        supervisor = self._agent_pool.get("supervisor")
        if not supervisor:
            raise RuntimeError("Supervisor agent not started")
        task_id = await supervisor.submit_task(
            TaskRequest(description=task_description, options=options or {})
        )
        return task_id

    async def emergency_stop(self) -> None:
        log.warning("runtime.PANIC - emergency stop triggered")
        await self.bus.publish(Event("system.panic", payload=None, priority=0))
        for agent in self._agent_pool.values():
            agent.emergency_stop()


@asynccontextmanager
async def runtime_context(
    config: AgentMaxConfig | None = None,
) -> AsyncGenerator[AgentMaxRuntime, None]:
    rt = AgentMaxRuntime(config)
    await rt.start()
    try:
        yield rt
    finally:
        await rt.shutdown()
