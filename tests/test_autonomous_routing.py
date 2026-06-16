import asyncio
import json
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

from core.agents.planning_agent import PlanningAgent, _build_capability_fallback_plan
from core.ai.ai_router import AIRouter
from core.ai.llamacpp_client import LlamaCppSidecarClient
from core.ai.lmstudio_client import LMStudioClient
from core.ai.prompt_templates import CHAT_SYSTEM_PROMPT
from core.ai.thinking_engine import ThinkingEngine
from core.config import AgentMaxConfig, AIConfig
from core.ipc import _should_auto_dispatch_task


def test_chat_prompt_allows_real_task_dispatch() -> None:
    assert '"action": "none|task"' in CHAT_SYSTEM_PROMPT
    assert "SupervisorAgent owns tools" in CHAT_SYSTEM_PROMPT
    assert 'action is ALWAYS "none"' not in CHAT_SYSTEM_PROMPT
    assert "[SANDBOX:" not in CHAT_SYSTEM_PROMPT


def test_autonomous_router_dispatches_operational_requests() -> None:
    assert _should_auto_dispatch_task("abre chrome")
    assert _should_auto_dispatch_task("habre chrome")
    assert _should_auto_dispatch_task("cierra el bloc de notas")
    assert _should_auto_dispatch_task("busca noticias de IA en internet")
    assert _should_auto_dispatch_task("haz click en el boton aceptar")
    assert _should_auto_dispatch_task("redacta un correo para mi jefe")


def test_autonomous_router_keeps_information_requests_in_chat() -> None:
    assert not _should_auto_dispatch_task("explica que es una ventana")
    assert not _should_auto_dispatch_task("como puedo abrir chrome?")


def test_planning_fallback_defers_complex_tasks_to_screenshot_only() -> None:
    # Without AI model, open/close/search/compose tasks all fall back to a
    # screenshot-only plan so the runtime can observe and replan.
    open_plan = _build_capability_fallback_plan("t1", "abre el bloc de notas")
    close_plan = _build_capability_fallback_plan("t2", "cierra chrome")
    search_plan = _build_capability_fallback_plan("t3", "busca AgentMax en internet")
    email_plan = _build_capability_fallback_plan("t4", "redacta un correo")

    for plan in (open_plan, close_plan, search_plan, email_plan):
        assert plan.steps[0]["type"] == "screenshot"
        assert len(plan.steps) == 1


def test_planning_fallback_builds_real_input_control_steps() -> None:
    # Fallback plans no longer pre-program mouse/keyboard actions.
    # The new fallback emits only a screenshot so the AI can re-plan.
    move_plan = _build_capability_fallback_plan("m1", "mueve el mouse a 500 300")
    click_plan = _build_capability_fallback_plan("c1", "haz click en 500 300")
    type_plan = _build_capability_fallback_plan("t1", "escribe hola mundo")

    for plan in (move_plan, click_plan, type_plan):
        assert len(plan.steps) == 1
        assert plan.steps[0]["type"] == "screenshot"


def test_thinking_core_accepts_fallback_mouse_move() -> None:
    engine = ThinkingEngine()
    # Fallback plan now produces only a screenshot step.
    # Construct the move_mouse step directly to test the engine's decision.
    step = {
        "type": "move_mouse",
        "x": 500,
        "y": 300,
        "description": "Move mouse to (500, 300)",
    }

    decision = engine.decide_step(None, step, confirmed=False)

    assert decision.allowed is True
    assert decision.normalized_step["type"] == "move_mouse"
    assert decision.normalized_step["x"] == 500
    assert decision.normalized_step["y"] == 300


def test_planning_normalizes_model_steps_passes_through_unchanged() -> None:
    # _normalize_model_steps is now a no-op: the model produces typed desktop
    # actions directly; shell shortcuts are no longer rewritten to navigate/close_app.
    normalizer = PlanningAgent.__new__(PlanningAgent)

    shell_step = {
        "type": "shell",
        "command": "echo hello",
        "critical": True,
    }
    result = normalizer._normalize_model_steps([shell_step])
    assert result == [shell_step]


def test_lmstudio_router_accepts_flat_ai_config() -> None:
    ai_config = AIConfig(
        backend="lmstudio",
        lmstudio_host="127.0.0.1",
        lmstudio_port=1235,
        model="local-model",
    )
    config = AgentMaxConfig(ai=ai_config)

    backend = AIRouter(config)._build_backend()

    assert isinstance(backend, LMStudioClient)
    assert backend._base_url == "http://127.0.0.1:1235/v1"
    asyncio.run(backend.close())


def test_llamacpp_router_accepts_gguf_backend() -> None:
    ai_config = AIConfig(
        backend="llamacpp",
        llama_cpp_host="127.0.0.1",
        llama_cpp_port=18080,
        llama_cpp_model_path=str(__file__),
        llama_cpp_server_bin="llama-server",
        model="fake-gguf",
    )
    config = AgentMaxConfig(ai=ai_config)

    backend = AIRouter(config)._build_backend()

    assert isinstance(backend, LlamaCppSidecarClient)
    assert backend.backend_name == "llamacpp"
    assert backend._base_url == "http://127.0.0.1:18080/v1"
    asyncio.run(backend.close())


def test_llamacpp_health_reports_missing_model_path() -> None:
    client = LlamaCppSidecarClient(
        AIConfig(
            backend="llamacpp",
            llama_cpp_model_path="",
            llama_cpp_server_bin="llama-server",
        )
    )

    health = asyncio.run(client.health_check())
    asyncio.run(client.close())

    assert health["ok"] is False
    assert "AGENTMAX_GGUF_MODEL_PATH" in health["error"]


def test_llamacpp_client_uses_existing_openai_compatible_server() -> None:
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):  # noqa: N802
            if self.path == "/v1/models":
                self._send({"data": [{"id": "fake-gguf"}]})
            else:
                self.send_response(404)
                self.end_headers()

        def do_POST(self):  # noqa: N802
            if self.path == "/v1/chat/completions":
                self.rfile.read(int(self.headers.get("Content-Length", "0")))
                self._send(
                    {
                        "choices": [{"message": {"content": "ok llama"}}],
                        "usage": {
                            "prompt_tokens": 3,
                            "completion_tokens": 2,
                            "total_tokens": 5,
                        },
                    }
                )
            else:
                self.send_response(404)
                self.end_headers()

        def log_message(self, *_args):  # noqa: ANN001
            return

        def _send(self, payload: dict) -> None:
            body = json.dumps(payload).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

    server = HTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    host, port = server.server_address
    client = LlamaCppSidecarClient(
        AIConfig(
            backend="llamacpp",
            llama_cpp_host=host,
            llama_cpp_port=port,
            llama_cpp_model_path=str(__file__),
            llama_cpp_server_bin="llama-server",
            model="fake-gguf",
        )
    )

    try:
        result = asyncio.run(client.text_query("system", "hello"))
        health = asyncio.run(client.health_check())
        assert result == "ok llama"
        assert health["ok"] is True
        assert health["models"] == ["fake-gguf"]
        assert client.usage_stats["total_tokens"] == 5
    finally:
        asyncio.run(client.close())
        server.shutdown()
        server.server_close()


def test_input_config_exposes_human_simulator_fields() -> None:
    config = AgentMaxConfig().input

    assert config.mouse_speed_px_per_sec > 0
    assert config.mouse_jitter_px >= 0
    assert config.bezier_control_variance >= 0
    assert config.typing_wpm > 0
