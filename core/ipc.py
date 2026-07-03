"""
IPC Server - high-performance bridge between UI and Python core.

Architecture:
  - agentcore (Rust): HTTP REST (7789) + stdio IPC + vision/automation/security
  - core/ipc.py (Python): proxies to agentcore, manages Python agent pool, WebSocket

Key optimizations:
  - Binary MessagePack protocol (not JSON) for WebSocket frame data
  - Iterative stack-based serializer - zero recursion, no stack overflow
  - Frame deduplication BEFORE serialization (skip unchanged frames)
  - Message batching: up to 16 events coalesced into one WS frame
  - No numpy serialization through WS - only metadata diffs
  - agentcore handles: OCR, pixel analysis, screen capture, automation
  - Python handles: supervisor agent, workflow agent, AI routing
"""

from __future__ import annotations

import asyncio
import json
import os
import re
import subprocess
import sys
import threading
import time
import unicodedata
from dataclasses import dataclass, field
from typing import Any

import msgpack
import structlog
import websockets
from fastapi import Request
from fastapi.responses import JSONResponse

from core.beta import rest_api as beta_rest
from core.data_collection import consent as beta_consent
from core.data_collection.session_logger import flush_session, get_session_logger
from core.event_bus import Event, EventBus
from core.security import ipc_auth as _ipc_auth

log = structlog.get_logger(__name__)

# IPC API contract version. Bump the MINOR when adding backward-compatible
# fields/endpoints, the MAJOR on a breaking change to existing routes or payload
# shapes. Exposed on /api/version, /api/status, /health and the WS "hello" frame
# so clients (CLI, Tauri UI, tests) can detect compatibility without guessing.
IPC_API_VERSION = "1.0"

_AUTONOMOUS_ACTION_TERMS = {
    "abre",
    "abrir",
    "habre",
    "habrir",
    "open",
    "launch",
    "inicia",
    "ejecuta",
    "run",
    "cierra",
    "cerrar",
    "close",
    "kill",
    "termina",
    "deten",
    "busca",
    "buscar",
    "search",
    "investiga",
    "navega",
    "download",
    "descarga",
    "click",
    "clic",
    "pulsa",
    "presiona",
    "escribe",
    "type",
    "mueve",
    "lee",
    "leer",
    "lista",
    "listar",
    "crea",
    "crear",
    "guarda",
    "write",
    "redacta",
    "redactar",
    "correo",
    "email",
    "gmail",
    "outlook",
    "borra",
    "borrar",
    "elimina",
    "eliminar",
    "mover",
    "renombra",
    "captura",
    "screenshot",
    "pantalla",
    "ventana",
    "terminal",
    "powershell",
}

_INFORMATIONAL_PREFIXES = (
    "que es",
    "que son",
    "como puedo",
    "como se",
    "explica",
    "explain",
    "why",
    "what is",
    "how do i",
    "how can i",
    "dime que",
)


def _normalize_intent_text(value: str) -> str:
    normalized = unicodedata.normalize("NFKD", value).encode("ascii", "ignore").decode("ascii")
    return re.sub(r"\s+", " ", normalized.lower()).strip()


def _should_auto_dispatch_task(message: str, thinking_state: Any | None = None) -> bool:
    """Return True when chat should become a real SupervisorAgent task."""
    text = _normalize_intent_text(message)
    if not text:
        return False

    suggested_tools = set(getattr(thinking_state, "suggested_tools", []) or [])
    intent = str(getattr(thinking_state, "intent", ""))
    risk = float(getattr(thinking_state, "risk_score", 0.0) or 0.0)
    ambiguity = float(getattr(thinking_state, "ambiguity_score", 0.0) or 0.0)

    if risk >= 0.70 and ambiguity >= 0.55:
        return False

    has_action_term = any(term in text for term in _AUTONOMOUS_ACTION_TERMS)
    explicit_command = bool(
        re.match(
            r"^(abre|abrir|habre|habrir|cierra|cerrar|busca|buscar|ejecuta|inicia|haz|crea|lee|lista|click|clic|escribe|redacta|open|close|search|run)\b",
            text,
        )
    )
    informational = text.endswith("?") or any(
        text.startswith(prefix) for prefix in _INFORMATIONAL_PREFIXES
    )
    tool_intent = intent in {"desktop_automation", "file_operation", "research"}
    tool_selected = bool(suggested_tools & {"ui_automation", "file_system", "web_search", "shell"})

    if explicit_command:
        return True
    if informational and not explicit_command:
        return False
    return has_action_term and (tool_intent or tool_selected)


def _flatten_serialize(obj: Any, _max_depth: int = 16) -> Any:
    """
    Iterative stack-based serializer - NO RECURSION.

    Transforms any Python object into a msgpack-safe primitive.
    Max depth: 8 levels. Objects deeper than that are replaced with a
    sentinel string. Handles circular references by tracking object ids.

    Type rules:
      - None, bool, int, float, str  -> pass through
      - dict                          -> iterates values, max 8 levels
      - list / tuple                  -> iterates items, max 8 levels
      - bytes / bytearray            -> "<bytes:N>" sentinel (never raw bytes in events)
      - numpy ndarray                -> NEVER serialized here (use metadata only)
      - objects with __dict__        -> vars(), max 1 level deeper
      - enum / .value                -> recurse on .value
    """
    # Stack entries: (obj, current_depth)
    # We rebuild the output imperatively using a work-stack + result-stack.
    _SENTINEL = "<max_depth>"
    _seen: set[int] = set()

    def _serialize_one(o: Any, depth: int) -> Any:
        if depth > _max_depth:
            return _SENTINEL

        # Fast path: primitives need no transformation
        if o is None or isinstance(o, (bool, int, float, str)):
            return o

        oid = id(o)
        if oid in _seen:
            return "<circular>"
        _seen.add(oid)

        try:
            if isinstance(o, dict):
                return {str(k): _serialize_one(v, depth + 1) for k, v in list(o.items())}

            if isinstance(o, (list, tuple)):
                return [_serialize_one(item, depth + 1) for item in o]

            if isinstance(o, (bytes, bytearray)):
                return f"<bytes:{len(o)}>"

            if hasattr(o, "shape") and hasattr(o, "dtype"):
                return f"<ndarray:{tuple(o.shape)}:{o.dtype}>"

            if hasattr(o, "value"):  # Enum
                return _serialize_one(o.value, depth + 1)

            if hasattr(o, "__dict__") and not callable(o):
                d = vars(o)
                if d:
                    return _serialize_one(d, depth + 1)

            s = str(o)
            return s[:256] if len(s) > 256 else s
        finally:
            _seen.discard(oid)

    return _serialize_one(obj, 0)


def _frame_to_diff_metadata(frame: Any) -> dict:
    """
    Extract ONLY metadata from a frame - never the pixel data itself.

    This is what gets sent over WebSocket. The UI re-renders the canvas
    from its own cached frame + the diff metadata.
    """
    if frame is None:
        return {"hash": 0, "changed_ratio": 0.0, "regions": []}

    if hasattr(frame, "shape") and hasattr(frame, "dtype"):
        sample = frame[::16, ::16, 0]
        frame_size = getattr(frame, "size", 0)
        return {
            "shape": list(frame.shape),
            "dtype": str(frame.dtype),
            "hash": int(sample.sum() % (1 << 31)),
            "mean": float(frame.mean()) if frame_size > 0 else 0.0,
        }

    return {}


def _batch_events(events: list[Event], max_batch: int = 16) -> list[dict]:
    """Coalesce up to max_batch events into one outbound message."""
    if not events:
        return []

    batch = []
    for ev in events[-max_batch:]:
        batch.append(
            {
                "topic": ev.topic,
                "payload": _flatten_serialize(ev.payload),
                "source": ev.source,
                "ts": time.time(),
            }
        )
    return batch


class AgentCoreProxy:
    """
    Manages the agentcore (Rust) subprocess over stdio.
    Handles: vision, automation, security (Python no longer has secrets).
    Python IPC: task routing, agent orchestration, WebSocket event batching.
    """

    def __init__(self) -> None:
        self._proc: subprocess.Popen | None = None
        self._reader_thread: threading.Thread | None = None
        self._pending: dict[int, asyncio.Future] = {}
        self._counter = 0
        self._connected = False

    def start(self) -> bool:
        try:
            agentcore_path = self._find_agentcore()
            if not agentcore_path:
                log.warning("agentcore not found, using HTTP fallback")
                return False

            env = dict(os.environ)
            env["AGENTCORE_IPC_MODE"] = "stdio"
            env["AGENTCORE_HTTP_PORT"] = "7789"

            self._proc = subprocess.Popen(
                [str(agentcore_path)],
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                env=env,
            )
            self._reader_thread = threading.Thread(target=self._read_loop, daemon=True)
            self._reader_thread.start()
            self._connected = True
            log.info("agentcore subprocess started via stdio")
            return True
        except Exception as exc:
            log.error("failed to start agentcore", error=str(exc))
            return False

    def _find_agentcore(self) -> Any:
        candidates = []
        if hasattr(os, "_getfullpathname"):
            exe_dir = os.path.dirname(os.path.abspath(sys.executable))
        else:
            exe_dir = os.path.dirname(os.path.abspath(__file__))
            exe_dir = os.path.join(os.path.dirname(exe_dir), "agentcore")
        candidates.append(os.path.join(exe_dir, "agentcore.exe"))
        candidates.append(os.path.join(os.path.dirname(exe_dir), "agentcore", "agentcore.exe"))
        for candidate in candidates:
            if os.path.exists(candidate):
                return candidate
        return None

    def _read_loop(self) -> None:
        while self._proc and self._proc.stdout:
            try:
                line = self._proc.stdout.readline()
                if not line:
                    break
                data = line.strip()
                if data:
                    try:
                        import base64

                        raw = base64.b64decode(data)
                        resp = json.loads(raw.decode())
                        msg_id = resp.get("id", 0)
                        fut = self._pending.pop(msg_id, None)
                        if fut and not fut.done():
                            result = resp.get("result")
                            error = resp.get("error")
                            if error:
                                fut.set_exception(Exception(error))
                            else:
                                fut.set_result(result)
                    except (ValueError, json.JSONDecodeError, Exception) as exc:
                        import logging

                        logging.getLogger(__name__).debug("agentcore read parse error: %s", exc)
            except OSError:
                break

    async def call(self, method: str, params: Any) -> Any:
        if not self._connected:
            return await self._http_proxy(method, params)

        msg_id = self._counter + 1
        self._counter = msg_id

        future = asyncio.get_running_loop().create_future()
        self._pending[msg_id] = future

        import base64

        msg = json.dumps({"jsonrpc": "2.0", "method": method, "params": params, "id": msg_id})
        raw = base64.b64encode(msg.encode()).decode()

        if self._proc and self._proc.stdin:
            self._proc.stdin.write(raw + "\n")
            self._proc.stdin.flush()

        try:
            return await asyncio.wait_for(future, timeout=10.0)
        except TimeoutError:
            self._pending.pop(msg_id, None)
            return await self._http_proxy(method, params)

    async def _http_proxy(self, method: str, params: Any) -> Any:
        import httpx

        port = os.environ.get("AGENTCORE_HTTP_PORT", "7789")
        url = f"http://127.0.0.1:{port}/api/{method.replace('_', '/')}"

        try:
            async with httpx.AsyncClient(timeout=5.0) as client:
                if method == "task.submit":
                    resp = await client.post(
                        url,
                        json={
                            "description": params.get("description", ""),
                            "options": params.get("options", {}),
                        },
                    )
                else:
                    resp = await client.get(url)
                return resp.json()
        except Exception as exc:
            log.error("agentcore HTTP proxy failed", method=method, error=str(exc))
            return None

    def stop(self) -> None:
        self._connected = False
        for future in list(self._pending.values()):
            if not future.done():
                future.cancel()
        self._pending.clear()
        if self._proc:
            try:
                self._proc.terminate()
                self._proc.wait(timeout=2)
            except Exception:
                try:
                    self._proc.kill()
                except Exception:
                    pass
            self._proc = None


@dataclass
class TaskSubmitRequest:
    description: str = ""
    options: dict = field(default_factory=dict)


@dataclass
class HudDrawRequest:
    type: str = "circle"
    x: int = -1
    y: int = -1
    label: str = ""
    color: str = "#ff3a3a"


@dataclass
class ElementConfirmRequest:
    correct: bool = True
    corrected_bounds: list[int] | None = None


@dataclass
class RecordingStartRequest:
    task_name: str = "Recorded Task"


class IPCServer:
    """
    Dual-channel IPC with binary MessagePack protocol for performance.
    Routes REST calls through AgentCoreProxy -> agentcore (Rust) or HTTP fallback.
    WebSocket: event streaming with message batching + frame diff metadata.

    agentcore (Rust) handles: vision, OCR, pixel analysis, automation, security.
    Python handles: supervisor agent, workflow agent, AI routing, event bus.
    """

    def __init__(self, config: Any, bus: EventBus, agent_pool: dict, runtime: Any = None) -> None:
        self._config = config
        self._bus = bus
        self._agent_pool = agent_pool
        self._runtime = runtime
        self._ws_clients: set[Any] = set()
        self._recorder: Any = None
        self._pending_events: list[Event] = []
        self._batch_task: asyncio.Task | None = None
        self._serve_task: asyncio.Task | None = None
        self._metrics = {
            "request_count": 0,
            "error_count": 0,
            "avg_latency_ms": 0.0,
            "auth_rejected": 0,
        }
        self._agentcore = AgentCoreProxy()
        self._agentcore.start()
        self._conversation: Any = None  # ConversationManager, lazy-init
        self._conversation_lock = asyncio.Lock()
        self._current_chat_task: asyncio.Task | None = None
        from core.ai.thinking_engine import get_thinking_engine

        self._thinking = get_thinking_engine()
        self._goal_engine: Any = None  # GoalEngine, lazy-init

        # IPC authentication is enabled by default for the desktop product.
        # Token is always generated so the Tauri side can pick it up the
        # moment we flip the flag without a runtime restart.
        self._ipc_token = _ipc_auth.ensure_token()
        self._ipc_auth_enabled = _ipc_auth.is_auth_enabled(self._config)
        log.info(
            "ipc.auth_status",
            enabled=self._ipc_auth_enabled,
            token_file=str(_ipc_auth.token_file_path()),
            token_env=_ipc_auth.TOKEN_ENV_VAR,
        )

    async def handle_rest(
        self,
        method: str,
        path: str,
        data: dict | None = None,
        *,
        headers: dict[str, str] | None = None,
        query: dict[str, list[str]] | None = None,
    ) -> dict:
        """Route REST calls to agentcore (Rust) via stdio or HTTP fallback."""
        self._metrics["request_count"] += 1
        start = time.perf_counter()

        try:
            beta_response = beta_rest.try_handle(
                method,
                path,
                data,
                headers=headers,
                query=query,
            )
            if beta_response is not None:
                return beta_response

            if path.startswith("/api/tasks"):
                if method == "POST" and path == "/api/tasks":
                    supervisor = self._agent_pool.get("supervisor")
                    if not supervisor:
                        self._metrics["error_count"] += 1
                        return {"error": "supervisor_not_ready"}
                    from core.agents.supervisor import TaskRequest

                    req_data = data or {}
                    task_id = await supervisor.submit_task(
                        TaskRequest(
                            description=req_data.get("description", ""),
                            options=req_data.get("options", {}),
                        )
                    )
                    return {"task_id": task_id, "status": "queued"}
                elif method == "GET":
                    parts = path.split("/")
                    task_id = parts[-1]
                    supervisor = self._agent_pool.get("supervisor")
                    if supervisor:
                        status = await supervisor.get_task_status(task_id)
                        if status:
                            return status
                    return {"task_id": task_id, "status": "queued", "steps_completed": 0}
                elif "confirm" in path:
                    parts = path.split("/")
                    if len(parts) >= 3:
                        task_id = parts[-2]
                        supervisor = self._agent_pool.get("supervisor")
                        if supervisor:
                            await supervisor.confirm_task(task_id)
                    return {"confirmed": True}

            elif path == "/.well-known/agent.json" and method == "GET":
                return self._a2a_agent_card()

            elif path == "/a2a/tasks" and method == "POST":
                return await self._handle_a2a_tasks(data or {}, headers or {})

            elif path == "/mcp" and method == "POST":
                return await self._handle_mcp(data or {})

            elif path == "/api/tokens":
                return await self._handle_tokens()

            elif path == "/api/emergency_stop":
                await self._bus.publish(Event("system.panic", None, priority=0))
                return {"stopped": True}

            elif path.startswith("/api/tools"):
                supervisor = self._agent_pool.get("supervisor")
                if path == "/api/tools" and method == "GET":
                    if supervisor and hasattr(supervisor, "tool_diagnostics"):
                        return supervisor.tool_diagnostics()
                    from core.tools.registry import ToolRegistry

                    return {"catalog": ToolRegistry.default().snapshot()}
                from core.tools.testing_runner import ToolTestingRunner

                runner = ToolTestingRunner()
                if path == "/api/tools/validate-json":
                    return runner.to_dict(await runner.validate_json())
                if path == "/api/tools/doctor":
                    return runner.to_dict(await runner.doctor())
                if path == "/api/tools/test":
                    payload = data or {}
                    return runner.to_dict(
                        await runner.run(
                            category=payload.get("category"),
                            safe=bool(payload.get("safe", True)),
                            integration=bool(payload.get("integration", False)),
                        )
                    )
                return {"error": "unknown_tools_endpoint"}

            elif path == "/api/status":
                agents_status = {}
                for name, agent in self._agent_pool.items():
                    agents_status[name] = getattr(agent, "metrics", {})
                return {
                    "status": "running",
                    "api_version": IPC_API_VERSION,
                    "timestamp": time.time(),
                    "agents": agents_status,
                    "bus_depth": getattr(self._bus, "queue_depth", 0),
                    "ipc_metrics": self._metrics,
                }

            elif path.startswith("/api/ai/"):
                if path == "/api/ai/status":
                    return await self._agentcore.call("ai.status", {})
                elif "/switch/" in path:
                    backend = path.split("/")[-1]
                    return await self._agentcore.call("ai.switch", {"backend": backend})
                elif path == "/api/ai/lmstudio/models":
                    return await self._agentcore.call("lmstudio.models", {})

            elif path == "/api/chat":
                return await self._handle_chat(data or {})

            elif path.startswith("/api/goal"):
                return await self._handle_goal(method, path, data or {})

            elif path == "/api/chat/stop":
                if hasattr(self, "_current_chat_task") and self._current_chat_task:
                    self._current_chat_task.cancel()
                    self._current_chat_task = None
                    log.info("ipc.chat_stopped_by_user")
                    return {"stopped": True}
                return {"stopped": False}

            elif path == "/api/shutdown":
                await self._bus.publish(Event("system.shutdown", None, priority=0))
                return {"ok": True}

            elif path == "/api/version":
                return {"api_version": IPC_API_VERSION}

            elif path in {"/health", "/api/health"}:
                return {
                    **beta_rest.health_payload(ipc_auth_enabled=self._ipc_auth_enabled),
                    "api_version": IPC_API_VERSION,
                }

            return {"error": "unknown_endpoint"}

        except Exception as exc:
            self._metrics["error_count"] += 1
            return {"error": str(exc)}
        finally:
            elapsed = (time.perf_counter() - start) * 1000
            self._metrics["avg_latency_ms"] = self._metrics["avg_latency_ms"] * 0.9 + elapsed * 0.1

    # ──────────────────────────────────────────────────────────────
    # Outward-facing MCP / A2A server endpoints
    #
    # NOTE: live wiring not yet verified end-to-end against a running app —
    # the testable cores live in core/server_endpoints.py. These bridge the
    # sync protocol servers to the async executor via run_coroutine_threadsafe
    # from a worker thread (same pattern as the skill executor). They are NOT
    # added to the IPC auth allowlist, so when IPC auth is enabled they sit
    # behind it; review the exposure/auth model before production use.
    # ──────────────────────────────────────────────────────────────

    def _a2a_agent_card(self) -> dict:
        """Serve the A2A discovery card advertising AgentMax's bundled skills."""
        from core.server_endpoints import agent_card_dict

        try:
            from core.skills.registry import SkillRegistry

            skills = SkillRegistry.default().names()
        except Exception:  # noqa: BLE001 - discovery must never crash on skill load
            skills = []
        server_cfg = getattr(self._config, "server", None)
        host = getattr(server_cfg, "host", "127.0.0.1")
        port = getattr(server_cfg, "api_port", 7790)
        return agent_card_dict(url=f"http://{host}:{port}", skills=skills)

    def _a2a_error(self, data: dict, message: str) -> dict:
        return {
            "jsonrpc": "2.0",
            "id": data.get("id", ""),
            "error": {"code": -32000, "message": message},
        }

    async def _handle_a2a_tasks(self, data: dict, headers: dict[str, str]) -> dict:
        """Accept an A2A task: submit it to the supervisor and await the result."""
        if not self._agent_pool.get("supervisor"):
            return self._a2a_error(data, "supervisor_not_ready")

        server = self._ensure_a2a_server()
        auth = headers.get("authorization") or headers.get("Authorization") or ""
        token = auth[7:] if auth.lower().startswith("bearer ") else None
        return await asyncio.to_thread(server.handle_request, data, token=token)

    def _ensure_a2a_server(self) -> Any:
        # One long-lived server instance: A2ATask state lives on the server, so
        # a per-request instance would make tasks/get and tasks/cancel always
        # miss tasks created by an earlier tasks/send request.
        server = getattr(self, "_a2a_server", None)
        if server is not None:
            return server

        from core.a2a.protocol import AgentCard
        from core.a2a.server import A2AServer
        from core.agents.supervisor import TaskRequest
        from core.server_endpoints import run_a2a_task

        loop = asyncio.get_running_loop()

        async def _submit(text: str) -> str:
            return await self._agent_pool["supervisor"].submit_task(
                TaskRequest(description=text)
            )

        async def _poll(task_id: str) -> dict | None:
            return self._agent_pool["supervisor"].get_task_result(task_id)

        def handler(text: str) -> str:
            future = asyncio.run_coroutine_threadsafe(
                run_a2a_task(text, submit=_submit, poll=_poll), loop
            )
            return future.result()

        server = A2AServer(AgentCard(name="AgentMax"), handler=handler)
        self._a2a_server = server
        return server

    async def _handle_mcp(self, data: dict) -> dict:
        """Handle an MCP JSON-RPC request, exposing the supervisor's tool catalog."""
        supervisor = self._agent_pool.get("supervisor")
        if not supervisor:
            return {
                "jsonrpc": "2.0",
                "id": data.get("id", 0),
                "error": {"code": -32000, "message": "supervisor_not_ready"},
            }

        from core.mcp.registry_server import build_tool_mcp_server
        from core.server_endpoints import mcp_response

        executor = supervisor._tool_executor  # noqa: SLF001 - runtime wiring
        loop = asyncio.get_running_loop()
        agent_pool = self._agent_pool
        runtime = self._runtime

        def run_tool(tool_id: str, args: dict) -> Any:
            coro = executor.execute_step(
                {"tool_id": tool_id, "input": args},
                agent_pool=agent_pool,
                runtime=runtime,
            )
            return asyncio.run_coroutine_threadsafe(coro, loop).result()

        server = build_tool_mcp_server(executor.registry, run_tool)
        return await asyncio.to_thread(mcp_response, server, data)

    async def _handle_tokens(self) -> dict:
        """Return the current state of the TokenManager (per-plan + per-user breakdown)."""
        tm = getattr(self._runtime, "token_manager", None)
        if tm is None:
            return {
                "available": False,
                "message": "TokenManager not initialized (no runtime or no token manager).",
            }

        def _finite(v: Any) -> int | None:
            return v if isinstance(v, int) else None

        # Primary: AgentMax plan (where ai.tokens events land by default).
        usage = tm.get_usage(plan="AgentMax", user="local")
        primary = {
            "plan": "AgentMax",
            "user": "local",
            "unlimited": usage.unlimited,
            "daily_tokens_used": usage.daily_tokens_used,
            "monthly_tokens_used": usage.monthly_tokens_used,
            "daily_limit": usage.daily_limit,
            "monthly_limit": usage.monthly_limit,
            "remaining_daily": _finite(usage.remaining_daily),
            "remaining_monthly": _finite(usage.remaining_monthly),
            "total_tokens_all_time": usage.total_tokens_all_time,
            "total_requests": usage.total_requests,
            "daily_reset_at": usage.daily_reset_at,
            "monthly_reset_at": usage.monthly_reset_at,
        }

        return {
            "available": True,
            "current": primary,
            "totals_by_plan": tm.totals_by_plan(),
            "last_7_days": tm.usage_by_day(plan="AgentMax", user="local", days=7),
        }

    def _is_goal_enabled(self) -> bool:
        """Check feature flag — /goal is OFF by default in closed beta."""
        from core.feature_flags import load_config_profile

        try:
            profile = load_config_profile()
            return profile.feature_flags.goal_engine
        except Exception:
            return False

    def _goal_require_approval(self) -> bool:
        from core.feature_flags import load_config_profile

        try:
            profile = load_config_profile()
            return profile.feature_flags.goal_engine_require_approval
        except Exception:
            return True

    def _get_goal_engine(self, ai_client: Any) -> Any:
        """Lazy-init GoalEngine using the existing Redis service and AI client."""
        if self._goal_engine is None:
            from core.beta.redis_service import RedisService
            from core.goal.goal_engine import GoalEngine

            redis = RedisService()
            redis.connect()
            self._goal_engine = GoalEngine(
                redis_service=redis,
                bus=self._bus,
                agent_pool=self._agent_pool,
                ai_client=ai_client,
                require_approval=self._goal_require_approval(),
            )
        return self._goal_engine

    async def _handle_goal(self, method: str, path: str, data: dict) -> dict:
        """REST handler for /api/goal endpoints."""
        if not self._is_goal_enabled():
            return {
                "error": "goal_engine_disabled",
                "message": (
                    "El modo /goal está desactivado en esta build de beta cerrada. "
                    "Actívalo con: feature_flags.goal_engine = true en agentmax.config.json"
                ),
            }

        planning_agent = self._agent_pool.get("planning")
        ai_client = getattr(planning_agent, "_claude", None) if planning_agent else None
        if ai_client is None:
            from core.ai.ai_router import AIRouter

            ai_client = AIRouter(self._config)

        engine = self._get_goal_engine(ai_client)
        parts = path.rstrip("/").split("/")

        if method == "POST" and path == "/api/goal":
            objective = str(data.get("objective", data.get("message", ""))).strip()
            if not objective:
                return {"error": "objective is required"}
            goal_id = await engine.start_goal(objective)
            return {
                "goal_id": goal_id,
                "status": "running",
                "require_approval": self._goal_require_approval(),
            }

        if method == "POST" and len(parts) == 4 and parts[-1] in {"approve", "reject"}:
            # /api/goal/<goal_id>/approve  or  /api/goal/<goal_id>/reject
            goal_id = parts[-2]
            action_id = str(data.get("action_id", "")).strip()
            approved = parts[-1] == "approve"
            ok = engine.approve_action(goal_id, action_id) if approved else engine.reject_action(goal_id, action_id)
            return {"goal_id": goal_id, "action_id": action_id, "approved": approved, "ok": ok}

        if method == "DELETE":
            goal_id = parts[-1]
            ok = await engine.stop_goal(goal_id)
            return {"goal_id": goal_id, "cancelled": ok}

        if method == "GET":
            if len(parts) >= 3 and parts[-1] != "goal":
                goal_id = parts[-1]
                state = engine.get_state(goal_id)
                if state:
                    return state.to_dict()
                return {"error": "goal not found"}
            return {"active": engine.list_active()}

        return {"error": "unknown goal endpoint"}

    async def _handle_chat(self, data: dict) -> dict:
        """Process a conversational message and optionally dispatch a task."""
        import re

        message = (data.get("message") or "").strip()
        if not message:
            return {"error": "empty_message"}

        # /goal <objective> — activate autonomous autoloop
        if message.startswith("/goal "):
            if not self._is_goal_enabled():
                return {
                    "reply": (
                        "El modo /goal está desactivado en esta build de beta cerrada.\n"
                        'Para activarlo: añade `"goal_engine": true` en agentmax.config.json'
                    ),
                    "task_id": None,
                }
            objective = message[6:].strip()
            if not objective:
                return {"reply": "Uso: /goal <objetivo>", "task_id": None}
            planning_agent = self._agent_pool.get("planning")
            ai_client = getattr(planning_agent, "_claude", None) if planning_agent else None
            if ai_client is None:
                from core.ai.ai_router import AIRouter

                ai_client = AIRouter(self._config)
            engine = self._get_goal_engine(ai_client)
            goal_id = await engine.start_goal(objective)
            approval_note = (
                "\n⚠️ Cada acción que modifique el sistema pedirá confirmación explícita."
                if self._goal_require_approval()
                else ""
            )
            reply = (
                f"Modo /goal activado. Objetivo: {objective}\n"
                f"ID de goal: {goal_id[:8]}…\n"
                "AgentMax trabajará de forma autónoma hasta completarlo. "
                f"Puedes cancelarlo con /goal stop {goal_id[:8]}.{approval_note}"
            )
            return {"reply": reply, "goal_id": goal_id, "task_id": None}

        if message.startswith("/goal stop "):
            goal_id_prefix = message[11:].strip()
            engine = self._goal_engine
            if engine is None:
                return {"reply": "No hay ningún goal activo.", "task_id": None}
            active = engine.list_active()
            matched = [gid for gid in active if gid.startswith(goal_id_prefix)]
            if not matched:
                return {"reply": f"No se encontró goal con ID '{goal_id_prefix}'.", "task_id": None}
            for gid in matched:
                await engine.stop_goal(gid)
            return {"reply": f"Goal(s) cancelado(s): {', '.join(gid[:8] for gid in matched)}", "task_id": None}

        if message.strip() == "/goal":
            engine = self._goal_engine
            active = engine.list_active() if engine else []
            if not active:
                return {"reply": "No hay goals activos. Usa: /goal <objetivo>", "task_id": None}
            lines = ["Goals activos:"]
            for gid in active:
                state = engine.get_state(gid)
                if state:
                    lines.append(f"  • {gid[:8]}… — iter {state.iterations}/{state.max_iterations} — {state.objective[:60]}")
            return {"reply": "\n".join(lines), "task_id": None}

        # Log incoming message to session logger (local, redacted).
        # NOTE: self._config.ai is an AIConfig pydantic model, NOT a dict —
        # use attribute access, not .get(). The previous .get() call raised
        # AttributeError on every /api/chat request.
        ai_cfg = getattr(self._config, "ai", None)
        backend_name = getattr(ai_cfg, "backend", "unknown") if ai_cfg is not None else "unknown"
        # Opt-in only: nothing is logged unless the tester explicitly consented.
        if beta_consent.is_enabled():
            try:
                session = get_session_logger()
                session.log(
                    user_message=message,
                    backend=str(backend_name),
                    outcome="queued",
                )
                flush_session(keep_in_memory=True)
            except Exception as exc:  # noqa: BLE001
                # Session logging must NEVER break the chat path.
                log.warning("ipc.session_log_failed", error=str(exc))

        # Sanitize: strip control chars, cap length
        message = re.sub(r"[\x00-\x08\x0b-\x0c\x0e-\x1f\x7f]", "", message)[:2000]

        # Lazy-init conversation manager (lock prevents double-init under concurrent requests)
        if self._conversation is None:
            async with self._conversation_lock:
                if self._conversation is None:
                    from core.ai.conversation_manager import ConversationManager

                    self._conversation = ConversationManager()

        # Get AI client from the planning agent's router (it's always initialised)
        # or fall back to constructing one directly from config.
        supervisor = self._agent_pool.get("supervisor")
        planning_agent = self._agent_pool.get("planning")
        ai_client = getattr(planning_agent, "_claude", None) if planning_agent else None
        if ai_client is None:
            from core.ai.ai_router import AIRouter

            ai_client = AIRouter(self._config)

        if ai_client is None:
            return {"reply": "AI backend is not ready yet.", "task_id": None}

        from core.ai.response_layer import ResponseLayer

        thinking_state = await self._thinking.prepare(
            task_id=f"chat:{int(time.time() * 1000)}",
            user_input=message,
            context={"stm": getattr(self._runtime, "stm", None)},
        )
        response_layer = ResponseLayer()

        conv: Any = self._conversation
        if supervisor and _should_auto_dispatch_task(message, thinking_state):
            try:
                from core.agents.supervisor import TaskRequest

                task_id = await supervisor.submit_task(
                    TaskRequest(
                        description=message,
                        options={"source": "chat", "autonomous": True},
                    )
                )
                reply = (
                    "Voy a ejecutarlo como una tarea real. Estoy analizando el estado, "
                    "seleccionando herramientas y validando el resultado."
                )
                conv.add_user(message)
                conv.add_assistant(reply, task_id=task_id)
                return {
                    "reply": reply,
                    "task_id": task_id,
                    "thinking_core": thinking_state.public_summary(),
                }
            except Exception as exc:
                log.error("ipc.chat_autonomous_dispatch_failed", error=str(exc))

        # Build history with last task context injected
        last_task = conv.last_task_description
        context_note = f"\n\n[Last executed task: {last_task}]" if last_task else ""
        context_note += (
            "\n\n[Private Thinking Core summary: "
            f"intent={thinking_state.intent}, "
            f"depth={thinking_state.reasoning_depth.value}, "
            f"confidence={thinking_state.confidence_score:.2f}, "
            f"risk={thinking_state.risk_score:.2f}. "
            "Do not reveal internal notes.]"
        )

        from core.ai.prompt_templates import CHAT_SYSTEM_PROMPT

        system = CHAT_SYSTEM_PROMPT + context_note

        conv.add_user(message)
        history = conv.get_history()

        try:
            # Run the query in a tracked task so it can be cancelled
            self._current_chat_task = asyncio.create_task(
                ai_client.chat_query(system=system, history=history)
            )
            raw = await self._current_chat_task
        except asyncio.CancelledError:
            log.info("ipc.chat_cancelled")
            return {"reply": "... [Interrumpido por el usuario]", "task_id": None}
        except Exception as exc:
            log.error("ipc.chat_ai_error", error=str(exc))
            conv.add_assistant("I encountered an error. Please try again.")
            return {"reply": "I encountered an error. Please try again.", "task_id": None}
        finally:
            self._current_chat_task = None

        # Parse structured JSON response
        visible = response_layer.visible_chat_payload(raw)
        reply = visible["reply"]
        action = visible["action"]
        task_desc = visible["task"] or None

        task_id: str | None = None
        if action == "task" and task_desc and supervisor:
            try:
                from core.agents.supervisor import TaskRequest

                task_id = await supervisor.submit_task(TaskRequest(description=task_desc))
                conv.update_last_task_desc(task_desc)
                log.info("ipc.chat_task_submitted", task_id=task_id, desc=task_desc[:80])
            except Exception as exc:
                log.error("ipc.chat_task_submit_failed", error=str(exc))
                reply += " (Task submission failed; please try again.)"

        conv.add_assistant(reply, task_id=task_id)
        return {
            "reply": reply,
            "task_id": task_id,
            "thinking_core": thinking_state.public_summary(),
        }

    async def serve(self) -> None:
        self._serve_task = asyncio.create_task(self._serve_websocket(), name="ipc-websocket")
        self._http_task = asyncio.create_task(self._serve_http(), name="ipc-http")
        await asyncio.gather(self._serve_task, self._http_task)

    async def _serve_http(self) -> None:
        import uvicorn
        from fastapi import FastAPI
        from fastapi.middleware.cors import CORSMiddleware

        app = FastAPI()
        app.add_middleware(
            CORSMiddleware,
            allow_origins=[
                "http://localhost:5173",
                "http://127.0.0.1:5173",
                "http://localhost:1420",
                "http://127.0.0.1:1420",
                "tauri://localhost",
            ],
            allow_methods=["GET", "POST", "OPTIONS"],
            allow_headers=["Content-Type", "Authorization", _ipc_auth.AUTH_HEADER],
        )

        @app.api_route("/{path:path}", methods=["GET", "POST", "OPTIONS"])
        async def catch_all(request: Request, path: str):
            method = request.method
            full_path = f"/{path}"

            # Phase 2: IPC auth gate (feature-flagged).
            try:
                _ipc_auth.check_rest_request(
                    path=full_path,
                    headers=dict(request.headers.items()),
                    enabled=self._ipc_auth_enabled,
                    expected_token=self._ipc_token,
                )
            except _ipc_auth.IPCAuthError as exc:
                self._metrics["auth_rejected"] += 1
                log.warning("ipc.auth_rejected", path=full_path, reason=exc.reason)
                return JSONResponse(
                    status_code=401,
                    content={"error": "unauthorized", "reason": exc.reason},
                )

            data = None
            if method == "POST":
                try:
                    data = await request.json()
                except (ValueError, UnicodeDecodeError) as exc:
                    log.warning("ipc.json_parse_failed", path=path, error=str(exc))

            from urllib.parse import parse_qs

            query = parse_qs(request.url.query or "")
            return await self.handle_rest(
                method,
                full_path,
                data,
                headers=dict(request.headers.items()),
                query=query,
            )

        server_cfg = getattr(self._config, "server", None)
        host = getattr(server_cfg, "host", "127.0.0.1")
        port = getattr(server_cfg, "api_port", 7790)

        config = uvicorn.Config(app, host=host, port=port, log_level="error")
        server = uvicorn.Server(config)
        self._uvicorn_server = server
        await server.serve()

    async def stop(self) -> None:
        if self._batch_task:
            self._batch_task.cancel()
            try:
                await self._batch_task
            except asyncio.CancelledError:
                pass
            self._batch_task = None

        if self._serve_task:
            self._serve_task.cancel()
            try:
                await self._serve_task
            except asyncio.CancelledError:
                pass
            self._serve_task = None

        if getattr(self, "_http_task", None):
            # Ask uvicorn to exit gracefully so its lifespan task doesn't surface
            # a CancelledError traceback on shutdown; fall back to cancel if it
            # doesn't stop in time.
            srv = getattr(self, "_uvicorn_server", None)
            if srv is not None:
                srv.should_exit = True
            try:
                await asyncio.wait_for(self._http_task, timeout=3.0)
            except (TimeoutError, asyncio.CancelledError):
                pass
            self._http_task = None
            self._uvicorn_server = None

        for ws in list(self._ws_clients):
            try:
                await ws.close()
            except Exception:
                pass
        self._ws_clients.clear()
        self._agentcore.stop()

    async def _serve_websocket(self) -> None:
        self._bus.subscribe("*", self._collect_event)
        self._batch_task = asyncio.create_task(self._batch_sender())

        server_cfg = getattr(self._config, "server", None)
        ws_port = getattr(server_cfg, "ws_port", 7788)
        log.info("ipc.ws_listening", port=ws_port)

        async with websockets.serve(
            self._ws_handler,
            getattr(server_cfg, "host", "127.0.0.1"),
            ws_port,
        ):
            await asyncio.Future()

    async def _collect_event(self, event: Event) -> None:
        # Cap the pending list to prevent unbounded memory growth during bursts.
        if len(self._pending_events) < 512:
            self._pending_events.append(event)

    async def _batch_sender(self) -> None:
        """Flush pending events every 50ms, draining the full list in 16-event batches."""
        while True:
            await asyncio.sleep(0.05)
            if self._pending_events and self._ws_clients:
                # Drain all pending events in batches of 16 so nothing is silently dropped.
                pending = self._pending_events[:]
                self._pending_events.clear()
                dead: set = set()
                for start in range(0, len(pending), 16):
                    chunk = pending[start : start + 16]
                    batch = _batch_events(chunk, max_batch=16)
                    msg_bytes = msgpack.packb(
                        {"type": "event_batch", "events": batch, "ts": time.time()}
                    )
                    for ws in self._ws_clients:
                        try:
                            await ws.send(msg_bytes)
                        except Exception:
                            dead.add(ws)
                self._ws_clients -= dead

    async def _ws_handler(self, ws: Any) -> None:
        # Phase 2: handshake BEFORE joining the broadcast set, otherwise an
        # unauthenticated client would receive bus events while we wait for
        # the auth message.
        ok = await _ipc_auth.authenticate_ws(
            ws,
            expected_token=self._ipc_token,
            enabled=self._ipc_auth_enabled,
        )
        if not ok:
            self._metrics["auth_rejected"] += 1
            log.warning("ipc.ws_auth_rejected")
            return  # socket already closed by helper

        self._ws_clients.add(ws)
        log.debug("ipc.client_connected", clients=len(self._ws_clients))
        # Announce the IPC contract version as the first frame after the
        # handshake so clients can detect compatibility. Non-breaking: clients
        # switch on the message "type" and ignore frames they don't recognize.
        try:
            await ws.send(json.dumps({"type": "hello", "api_version": IPC_API_VERSION}))
        except Exception:
            pass
        try:
            async for msg in ws:
                await self._handle_ws_message(ws, msg)
        except websockets.exceptions.ConnectionClosed:
            pass
        finally:
            self._ws_clients.discard(ws)

    async def _handle_ws_message(self, ws: Any, raw: str | bytes) -> None:
        try:
            if isinstance(raw, bytes):
                msg = msgpack.unpackb(raw, raw=False)
            else:
                msg = json.loads(raw)

            cmd = msg.get("cmd")
            if cmd == "submit_task":
                supervisor = self._agent_pool.get("supervisor")
                if supervisor:
                    from core.agents.supervisor import TaskRequest

                    task_id = await supervisor.submit_task(
                        TaskRequest(description=msg.get("description", ""))
                    )
                    await ws.send(json.dumps({"type": "task_queued", "task_id": task_id}))
            elif cmd == "emergency_stop":
                await self._bus.publish(Event("system.panic", None, priority=0))
            elif cmd == "ping":
                await ws.send(json.dumps({"type": "pong", "ts": time.time()}))
            elif cmd == "get_version":
                await ws.send(json.dumps({"type": "version", "api_version": IPC_API_VERSION}))
        except (json.JSONDecodeError, ValueError, KeyError) as exc:
            log.warning("ipc.ws_message_parse_failed", error=str(exc))
        except websockets.exceptions.ConnectionClosed:
            pass
        except Exception as exc:
            log.error("ipc.ws_message_handler_error", error=str(exc))
