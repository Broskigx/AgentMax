"""Regression tests for bugs found during the full-codebase sweep.

1. ``ActionToolPlanner._required_params_from_json`` must always return a tuple
   (it previously returned a bare ``"computer"`` string for legacy app.* ids,
   which iterated to characters and made every step look like it was missing
   params ``c``, ``o``, ``m`` …).
2. ``WebSearchAgent.search`` must URL-encode the query so spaces and reserved
   characters (``&``, ``#``, ``?``) don't break or truncate the request.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from core.agents import web_search_agent
from core.agents.web_search_agent import WebSearchAgent
from core.ai.planner import ActionToolPlanner

# ---------------------------------------------------------------------------
# planner: required-params extraction always returns a tuple
# ---------------------------------------------------------------------------


def test_required_params_from_json_returns_tuple_for_legacy_app_ids() -> None:
    planner = ActionToolPlanner()
    params = planner._required_params_from_json(
        {"id": "app.open", "input_schema": {"required": ["target"]}}
    )
    assert isinstance(params, tuple)
    assert params == ("target",)


def test_policy_from_json_required_params_is_a_tuple_of_names() -> None:
    planner = ActionToolPlanner()
    policy = planner._policy_from_json(
        {
            "id": "filesystem.write",
            "category": "filesystem",
            "input_schema": {"required": ["path", "content"]},
        }
    )
    # Must be the field names, not the characters of a stringified value.
    assert policy.required_params == ("path", "content")


def test_decide_does_not_flag_spurious_missing_params() -> None:
    planner = ActionToolPlanner()
    # write_file requires ("path", "content"); provide both and it must validate,
    # not reject on single-character "missing params".
    decision = planner.decide(
        {"type": "write_file", "path": "C:/Users/x/Documents/a.txt", "content": "hi"},
        confirmed=True,
    )
    assert decision.allowed, decision.reason


# ---------------------------------------------------------------------------
# web search: the query is URL-encoded
# ---------------------------------------------------------------------------


def _make_web_agent() -> WebSearchAgent:
    ctx = MagicMock()
    ctx.config = MagicMock()
    ctx.audit.log_event = AsyncMock()
    agent = WebSearchAgent(ctx)
    agent.bus = MagicMock()
    agent.bus.publish = AsyncMock()
    return agent


async def test_search_url_encodes_the_query(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: dict[str, str] = {}

    class _Resp:
        text = "<html></html>"

        def raise_for_status(self) -> None:
            return None

    class _Client:
        def __init__(self, *args: object, **kwargs: object) -> None:
            pass

        async def __aenter__(self) -> _Client:
            return self

        async def __aexit__(self, *exc: object) -> None:
            return None

        async def get(self, url: str) -> _Resp:
            captured["url"] = url
            return _Resp()

    monkeypatch.setattr(web_search_agent.httpx, "AsyncClient", _Client)

    agent = _make_web_agent()
    result = await agent.search({"query": "cats & dogs? #1"})

    assert result.success is True
    # Spaces and reserved characters must be percent-encoded, never raw.
    assert " " not in captured["url"]
    assert "cats+%26+dogs%3F+%231" in captured["url"]


def test_planner_registry_still_loads() -> None:
    # Guard against the sweep fix breaking JSON registry loading entirely.
    planner = ActionToolPlanner()
    assert isinstance(planner.registry, dict)
    assert "write_file" in planner.registry
