"""Import + dataclass construction smoke tests for the AgentMax bot module.

These guard against dataclass field-ordering regressions: a non-default field
declared after a defaulted one makes the whole module fail to import, which the
rest of the suite never exercised.
"""

from __future__ import annotations

from datetime import UTC, datetime


def test_agentmax_bot_module_imports():
    import core.ai.agentmax_bot.agentmax_bot as bot  # noqa: F401


def test_bot_request_requires_message_only():
    from core.ai.agentmax_bot.agentmax_bot import BotRequest

    req = BotRequest(message="hola")
    assert req.message == "hola"
    assert req.conversation_id is None
    assert req.attachments == []


def test_bot_conversation_constructs_with_required_fields():
    from core.ai.agentmax_bot.agentmax_bot import (
        AgentMaxBotVersion,
        BotConversation,
    )

    now = datetime.now(UTC)
    convo = BotConversation(
        conversation_id="c1",
        user_id="u1",
        version=next(iter(AgentMaxBotVersion)),
        created_at=now,
        last_message_at=now,
    )
    assert convo.conversation_id == "c1"
    assert convo.messages == []
    assert convo.is_active is True
