"""
AgentMax Bot - Custom AI Assistant

The proprietary AI model trained by AgentMax that becomes
available starting from Pro plan. This is AgentMax's own
trained AI assistant with specialized capabilities.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from enum import Enum
from typing import TYPE_CHECKING, Any
from uuid import uuid4

import structlog

if TYPE_CHECKING:
    from fastapi import APIRouter

log = structlog.get_logger(__name__)


class AgentMaxBotVersion(str, Enum):
    """Versions of AgentMax Bot available by subscription"""

    NONE = "none"
    TRIAL = "trial"
    BASIC = "basic"
    FULL = "full"
    ELITE = "elite"


class BotCapability(str, Enum):
    """Capabilities of AgentMax Bot"""

    CONVERSATION = "conversation"
    CODE_GENERATION = "code_generation"
    DATA_ANALYSIS = "data_analysis"
    AUTOMATION = "automation"
    IMAGE_UNDERSTANDING = "image_understanding"
    FINE_TUNING = "fine_tuning"
    CUSTOM_TRAINING = "custom_training"
    API_INTEGRATION = "api_integration"
    REAL_TIME_INFERENCE = "real_time_inference"
    PRIORITY_PROCESSING = "priority_processing"


@dataclass
class BotModelConfig:
    """Configuration for AgentMax Bot model"""

    version: AgentMaxBotVersion
    name: str
    description: str

    max_tokens: int
    temperature: float
    top_p: float

    capabilities: list[BotCapability]

    context_window: int
    knowledge_cutoff: str

    pricing_per_1k_tokens: float
    daily_token_limit: int

    rate_limit_rpm: int
    supports_streaming: bool
    supports_functions: bool


@dataclass
class BotConversationMessage:
    """Message in a conversation with AgentMax Bot"""

    message_id: str
    role: str  # "user", "assistant", "system"
    content: str

    timestamp: datetime

    metadata: dict[str, Any] = field(default_factory=dict)

    attachments: list[str] = field(default_factory=list)
    tools_used: list[str] = field(default_factory=list)

    tokens_used: int = 0
    model_version: str | None = None


@dataclass
class BotConversation:
    """Conversation session with AgentMax Bot"""

    conversation_id: str
    user_id: str

    version: AgentMaxBotVersion

    created_at: datetime
    last_message_at: datetime

    messages: list[BotConversationMessage] = field(default_factory=list)

    system_prompt: str | None = None
    custom_instructions: str | None = None

    metadata: dict[str, Any] = field(default_factory=dict)

    total_tokens: int = 0
    message_count: int = 0

    is_active: bool = True
    title: str | None = None


@dataclass
class BotRequest:
    """Request to AgentMax Bot"""

    message: str

    conversation_id: str | None = None
    attachments: list[dict[str, Any]] = field(default_factory=list)

    temperature: float | None = None
    max_tokens: int | None = None
    top_p: float | None = None

    system_prompt: str | None = None
    functions: list[dict[str, Any]] | None = None

    stream: bool = False
    version: AgentMaxBotVersion | None = None


@dataclass
class BotResponse:
    """Response from AgentMax Bot"""

    response_id: str
    conversation_id: str

    content: str
    finish_reason: str

    model_version: str

    usage: dict[str, int] = field(default_factory=dict)
    timing_ms: int = 0

    tool_calls: list[dict[str, Any]] = field(default_factory=list)

    metadata: dict[str, Any] = field(default_factory=dict)


# Bot model configurations
BOT_CONFIGS = {
    AgentMaxBotVersion.TRIAL: BotModelConfig(
        version=AgentMaxBotVersion.TRIAL,
        name="AgentMax Bot (Trial)",
        description="7-day free trial of AgentMax Bot",
        max_tokens=2048,
        temperature=0.7,
        top_p=0.9,
        capabilities=[
            BotCapability.CONVERSATION,
        ],
        context_window=4096,
        knowledge_cutoff="2024-12",
        pricing_per_1k_tokens=0.0,
        daily_token_limit=500,
        rate_limit_rpm=10,
        supports_streaming=True,
        supports_functions=False,
    ),
    AgentMaxBotVersion.FULL: BotModelConfig(
        version=AgentMaxBotVersion.FULL,
        name="AgentMax Bot Pro",
        description="Full version with all capabilities for Pro plan",
        max_tokens=8192,
        temperature=0.7,
        top_p=0.9,
        capabilities=[
            BotCapability.CONVERSATION,
            BotCapability.CODE_GENERATION,
            BotCapability.DATA_ANALYSIS,
            BotCapability.AUTOMATION,
            BotCapability.IMAGE_UNDERSTANDING,
            BotCapability.FINE_TUNING,
            BotCapability.API_INTEGRATION,
            BotCapability.REAL_TIME_INFERENCE,
        ],
        context_window=16384,
        knowledge_cutoff="2025-01",
        pricing_per_1k_tokens=0.003,
        daily_token_limit=150000,
        rate_limit_rpm=120,
        supports_streaming=True,
        supports_functions=True,
    ),
    AgentMaxBotVersion.ELITE: BotModelConfig(
        version=AgentMaxBotVersion.ELITE,
        name="AgentMax Bot Elite",
        description="Elite version with maximum capabilities and custom training",
        max_tokens=16384,
        temperature=0.6,
        top_p=0.95,
        capabilities=[
            BotCapability.CONVERSATION,
            BotCapability.CODE_GENERATION,
            BotCapability.DATA_ANALYSIS,
            BotCapability.AUTOMATION,
            BotCapability.IMAGE_UNDERSTANDING,
            BotCapability.FINE_TUNING,
            BotCapability.CUSTOM_TRAINING,
            BotCapability.API_INTEGRATION,
            BotCapability.REAL_TIME_INFERENCE,
            BotCapability.PRIORITY_PROCESSING,
        ],
        context_window=32768,
        knowledge_cutoff="2025-01",
        pricing_per_1k_tokens=0.005,
        daily_token_limit=500000,
        rate_limit_rpm=300,
        supports_streaming=True,
        supports_functions=True,
    ),
}


class AgentMaxBot:
    """
    The proprietary AgentMax AI Bot - available from Pro plan.

    This is AgentMax's own trained AI assistant that provides
    advanced conversation, code generation, and automation capabilities.

    When a *token_manager* is provided, token/rate limit checks delegate
    to the centralized TokenManager. In unlimited mode, all checks pass.
    """

    def __init__(
        self,
        version: AgentMaxBotVersion = AgentMaxBotVersion.BASIC,
        api_endpoint: str | None = None,
        api_key: str | None = None,
        token_manager: Any = None,
    ):
        self.version = version
        self.api_endpoint = api_endpoint
        self.api_key = api_key
        self.token_manager = token_manager

        self.config = BOT_CONFIGS.get(version)

        self._conversations: dict[str, BotConversation] = {}
        self._user_usage: dict[str, dict[str, Any]] = {}

        self._custom_prompts: dict[str, str] = {}
        self._function_handlers: dict[str, Callable] = {}

    def check_access(self, user_plan: str) -> bool:
        """Check if user plan has access to this bot version"""
        plan_access = {
            "free": AgentMaxBotVersion.TRIAL,
            "starter": AgentMaxBotVersion.NONE,
            "pro": AgentMaxBotVersion.FULL,
            "elite": AgentMaxBotVersion.ELITE,
        }

        allowed_version = plan_access.get(user_plan.lower(), AgentMaxBotVersion.NONE)

        return self.version.value >= allowed_version.value

    def get_available_version_for_plan(self, plan: str) -> AgentMaxBotVersion:
        """Get the appropriate bot version for a subscription plan"""
        plan_versions = {
            "free": AgentMaxBotVersion.TRIAL,
            "starter": AgentMaxBotVersion.NONE,
            "pro": AgentMaxBotVersion.FULL,
            "elite": AgentMaxBotVersion.ELITE,
        }

        return plan_versions.get(plan.lower(), AgentMaxBotVersion.NONE)

    async def create_conversation(
        self,
        user_id: str,
        title: str | None = None,
        system_prompt: str | None = None,
        custom_instructions: str | None = None,
    ) -> BotConversation:
        """Create a new conversation with AgentMax Bot"""

        conversation_id = str(uuid4())

        default_system_prompt = system_prompt or self._get_default_system_prompt()

        conversation = BotConversation(
            conversation_id=conversation_id,
            user_id=user_id,
            version=self.version,
            system_prompt=default_system_prompt,
            custom_instructions=custom_instructions,
            created_at=datetime.now(UTC),
            last_message_at=datetime.now(UTC),
            title=title,
        )

        self._conversations[conversation_id] = conversation

        # Add initial system message
        initial_message = BotConversationMessage(
            message_id=str(uuid4()),
            role="system",
            content=default_system_prompt,
            timestamp=datetime.now(UTC),
            model_version=self.config.name if self.config else None,
        )
        conversation.messages.append(initial_message)

        log.info(
            "AGENTMAX_bot_conversation_created",
            conversation_id=conversation_id,
            user_id=user_id,
            version=self.version.value,
        )

        return conversation

    async def send_message(self, user_id: str, request: BotRequest) -> BotResponse:
        """Send a message to AgentMax Bot and get response"""

        start_time = datetime.utcnow()

        # Get or create conversation
        if request.conversation_id and request.conversation_id in self._conversations:
            conversation = self._conversations[request.conversation_id]
        else:
            conversation = await self.create_conversation(
                user_id=user_id,
                title=request.message[:50] + "..."
                if len(request.message) > 50
                else request.message,
            )

        # Check rate limits
        await self._check_rate_limit(user_id)

        # Check token limits
        await self._check_token_limit(user_id)

        # Build messages for API
        messages = self._build_messages(conversation, request)

        # Add user message
        user_message = BotConversationMessage(
            message_id=str(uuid4()),
            role="user",
            content=request.message,
            timestamp=datetime.now(UTC),
            attachments=[a.get("type") for a in request.attachments],
        )
        conversation.messages.append(user_message)

        # Call the model (mock for now - in production would call actual API)
        response_content, tool_calls, usage = await self._call_model(
            messages=messages,
            temperature=request.temperature or self.config.temperature,
            max_tokens=request.max_tokens or self.config.max_tokens,
            top_p=request.top_p or self.config.top_p,
            functions=request.functions,
        )

        # Create response
        response = BotResponse(
            response_id=str(uuid4()),
            conversation_id=conversation.conversation_id,
            content=response_content,
            finish_reason="stop",
            model_version=self.config.name,
            usage=usage,
            timing_ms=int((datetime.utcnow() - start_time).total_seconds() * 1000),
            tool_calls=tool_calls,
        )

        # Add assistant message to conversation
        assistant_message = BotConversationMessage(
            message_id=str(uuid4()),
            role="assistant",
            content=response_content,
            timestamp=datetime.now(UTC),
            tools_used=[tc.get("function", {}).get("name") for tc in tool_calls]
            if tool_calls
            else [],
            tokens_used=usage.get("total_tokens", 0),
            model_version=self.config.name,
        )
        conversation.messages.append(assistant_message)

        # Update conversation stats
        conversation.last_message_at = datetime.now(UTC)
        conversation.total_tokens += usage.get("total_tokens", 0)
        conversation.message_count += 2

        # Update user usage
        self._update_user_usage(user_id, usage)

        log.info(
            "AGENTMAX_bot_message_sent",
            conversation_id=conversation.conversation_id,
            user_id=user_id,
            tokens_used=usage.get("total_tokens", 0),
            timing_ms=response.timing_ms,
        )

        return response

    async def send_message_streaming(
        self, user_id: str, request: BotRequest, on_chunk: Callable[[str], Awaitable]
    ) -> BotResponse:
        """Send message with streaming response"""

        request.stream = True

        response = await self.send_message(user_id, request)

        return response

    async def get_conversation(self, conversation_id: str) -> BotConversation | None:
        """Get a conversation by ID"""
        return self._conversations.get(conversation_id)

    async def list_conversations(
        self, user_id: str, limit: int = 50, active_only: bool = True
    ) -> list[BotConversation]:
        """List user's conversations"""

        conversations = [c for c in self._conversations.values() if c.user_id == user_id]

        if active_only:
            conversations = [c for c in conversations if c.is_active]

        conversations.sort(key=lambda c: c.last_message_at, reverse=True)

        return conversations[:limit]

    async def delete_conversation(self, conversation_id: str, user_id: str) -> bool:
        """Delete a conversation"""

        conversation = self._conversations.get(conversation_id)

        if not conversation or conversation.user_id != user_id:
            return False

        conversation.is_active = False

        return True

    async def get_user_usage(self, user_id: str) -> dict[str, Any]:
        """Get user's current usage statistics.

        Delegates to TokenManager when available.
        """
        cap = self.config.daily_token_limit if self.config else 0

        if self.token_manager is not None:
            tu = self.token_manager.get_usage(plan=user_id, daily_cap=cap)
            return {
                "daily_tokens_used": tu.daily_tokens_used,
                "daily_limit": tu.daily_limit or 0,
                "remaining": tu.remaining if isinstance(tu.remaining, int) else float("inf"),
                "reset_at": tu.reset_at,
                "unlimited": tu.unlimited,
                "total_tokens_all_time": tu.total_tokens_all_time,
                "total_requests": tu.total_requests,
            }

        # Fallback: legacy in-memory tracking
        today = datetime.now(UTC).date().isoformat()
        if user_id not in self._user_usage:
            return {
                "daily_tokens_used": 0,
                "daily_limit": cap,
                "remaining": cap,
                "reset_at": self._get_next_reset_time().isoformat(),
            }
        user_data = self._user_usage[user_id]
        daily_usage = user_data.get("daily", {}).get(today, {}).get("tokens", 0)
        return {
            "daily_tokens_used": daily_usage,
            "daily_limit": cap,
            "remaining": max(0, cap - daily_usage),
            "reset_at": self._get_next_reset_time().isoformat(),
            "total_conversations": user_data.get("total_conversations", 0),
            "total_messages": user_data.get("total_messages", 0),
            "total_tokens": user_data.get("total_tokens", 0),
        }

    def register_function(
        self, name: str, handler: Callable, description: str, parameters: dict[str, Any]
    ):
        """Register a custom function for the bot to use"""

        self._function_handlers[name] = handler

        log.info("AGENTMAX_bot_function_registered", function=name)

    def set_custom_prompt(self, prompt_key: str, prompt_content: str):
        """Set a custom system prompt"""

        self._custom_prompts[prompt_key] = prompt_content

    def get_capabilities(self) -> list[str]:
        """Get list of capabilities for current version"""

        if not self.config:
            return []

        return [cap.value for cap in self.config.capabilities]

    async def _check_rate_limit(self, user_id: str):
        """Check if user has exceeded rate limits"""

        if not self.config:
            return

        user_data = self._user_usage.get(user_id, {})
        minute_key = datetime.now(UTC).strftime("%Y-%m-%d %H:%M")

        requests_this_minute = user_data.get("minute_requests", {}).get(minute_key, 0)

        if requests_this_minute >= self.config.rate_limit_rpm:
            raise RateLimitError(
                f"Rate limit exceeded. Max {self.config.rate_limit_rpm} requests per minute."
            )

        # Update counter
        if user_id not in self._user_usage:
            self._user_usage[user_id] = {}
        if "minute_requests" not in self._user_usage[user_id]:
            self._user_usage[user_id]["minute_requests"] = {}

        self._user_usage[user_id]["minute_requests"][minute_key] = requests_this_minute + 1

    async def _check_token_limit(self, user_id: str):
        """Check if user has exceeded daily token limits.

        Delegates to TokenManager when available.
        """
        if not self.config:
            return

        if self.token_manager is not None:
            # TokenManager handles unlimited + limited modes.
            self.token_manager.check(
                plan=user_id,
                delta=0,  # just check, don't consume yet
                daily_cap=self.config.daily_token_limit,
            )
            return

        # Fallback: legacy in-memory tracking
        today = datetime.now(UTC).date().isoformat()
        user_data = self._user_usage.get(user_id, {})
        daily_tokens = user_data.get("daily", {}).get(today, {}).get("tokens", 0)

        if daily_tokens >= self.config.daily_token_limit:
            raise TokenLimitError(
                f"Daily token limit exceeded. Limit: {self.config.daily_token_limit} tokens/day"
            )

    def _update_user_usage(self, user_id: str, usage: dict[str, int]):
        """Update user's usage statistics.

        Delegates to TokenManager when available.
        """
        delta = usage.get("total_tokens", 0)

        if self.token_manager is not None:
            self.token_manager.consume(plan=user_id, delta=delta, requests=1)
            return

        # Fallback: legacy in-memory tracking
        today = datetime.now(UTC).date().isoformat()
        if user_id not in self._user_usage:
            self._user_usage[user_id] = {
                "daily": {},
                "total_conversations": 0,
                "total_messages": 0,
                "total_tokens": 0,
            }
        user_data = self._user_usage[user_id]
        if today not in user_data["daily"]:
            user_data["daily"][today] = {"tokens": 0, "requests": 0}
        user_data["daily"][today]["tokens"] += delta
        user_data["daily"][today]["requests"] += 1
        user_data["total_tokens"] += delta

    def _build_messages(
        self, conversation: BotConversation, request: BotRequest
    ) -> list[dict[str, Any]]:
        """Build message list for API call"""

        messages = []

        # Add system prompt
        if conversation.system_prompt:
            messages.append({"role": "system", "content": conversation.system_prompt})

        # Add custom instructions
        if conversation.custom_instructions:
            messages.append(
                {
                    "role": "system",
                    "content": f"Additional instructions: {conversation.custom_instructions}",
                }
            )

        # Add conversation history
        for msg in conversation.messages[-20:]:  # Last 20 messages
            messages.append({"role": msg.role, "content": msg.content})

        # Add current user message
        messages.append({"role": "user", "content": request.message})

        return messages

    async def _call_model(
        self,
        messages: list[dict[str, Any]],
        temperature: float,
        max_tokens: int,
        top_p: float,
        functions: list[dict[str, Any]] | None,
    ) -> tuple[str, list[dict[str, Any]], dict[str, int]]:
        """Call the real Anthropic Claude API."""
        import os

        api_key = os.environ.get("CLAUDE_API_KEY") or os.environ.get("ANTHROPIC_API_KEY")
        if not api_key:
            raise RuntimeError(
                "No API key found. Set CLAUDE_API_KEY or ANTHROPIC_API_KEY environment variable."
            )

        try:
            import anthropic
        except ImportError as exc:
            raise RuntimeError(
                "anthropic package not installed. Run: pip install anthropic"
            ) from exc

        client = anthropic.AsyncAnthropic(api_key=api_key)

        # Separate system messages from conversation messages
        system_parts: list[str] = []
        conv_messages: list[dict[str, Any]] = []
        for m in messages:
            if m["role"] == "system":
                system_parts.append(m["content"])
            else:
                conv_messages.append(m)

        system_text = "\n\n".join(system_parts) if system_parts else None

        create_kwargs: dict[str, Any] = {
            "model": "claude-haiku-4-5-20251001",
            "max_tokens": max_tokens,
            "temperature": temperature,
            "messages": conv_messages,
        }
        if system_text:
            create_kwargs["system"] = system_text
        if functions:
            create_kwargs["tools"] = [
                {
                    "name": f["name"],
                    "description": f.get("description", ""),
                    "input_schema": f.get("parameters", {}),
                }
                for f in functions
            ]

        response = await client.messages.create(**create_kwargs)

        content = ""
        tool_calls: list[dict[str, Any]] = []
        for block in response.content:
            if block.type == "text":
                content += block.text
            elif block.type == "tool_use":
                tool_calls.append({"name": block.name, "input": block.input})

        usage = {
            "prompt_tokens": response.usage.input_tokens,
            "completion_tokens": response.usage.output_tokens,
            "total_tokens": response.usage.input_tokens + response.usage.output_tokens,
        }

        log.info(
            "AGENTMAX_bot.api_call",
            model="claude-haiku-4-5-20251001",
            input_tokens=response.usage.input_tokens,
            output_tokens=response.usage.output_tokens,
        )

        return content, tool_calls, usage

    def _get_default_system_prompt(self) -> str:
        """Get default system prompt for the bot"""

        prompts = {
            AgentMaxBotVersion.TRIAL: """Eres AgentMax Bot en modo de prueba gratuita.
Tienes acceso limitado pero eres igualmente útil y amigable.
Responde de forma clara y concisa.
Este es un período de prueba de 7 días.""",
            AgentMaxBotVersion.FULL: """Eres AgentMax Bot Pro, el asistente de IA premium de AgentMax.
Tienes acceso completo a todas las capacidades:
- Conversación avanzada
- Generación de código
- Análisis de datos
- Comprensión de imágenes
- Fine-tuning personalizado
- Integración con APIs
- Automatización inteligente

Sé extremadamente útil, preciso y proporciona respuestas detalladas.
Puedes usar herramientas cuando sea necesario.
Responde en español con profesionalismo.""",
            AgentMaxBotVersion.ELITE: """Eres AgentMax Bot Elite, la versión más avanzada del asistente de IA de AgentMax.
Tienes acceso completo a todas las capacidades:
- Fine-tuning y entrenamiento personalizado
- Análisis complejo y científico
- Desarrollo de soluciones enterprise
- Integraciones avanzadas
- Procesamiento prioritario

Eres el asistente más capaz de AgentMax. Proporciona respuestas de máxima calidad.
Cuando sea apropiado, usa funciones y herramientas disponibles.
Tu conocimiento está actualizado hasta principios de 2025.
Responde con excelencia en español o inglés según prefiera el usuario.""",
        }

        return prompts.get(self.version, prompts[AgentMaxBotVersion.FULL])

    def _get_next_reset_time(self) -> datetime:
        """Get next daily token reset time (midnight UTC)"""

        now = datetime.now(UTC)
        tomorrow = now.date() + timedelta(days=1)

        return datetime.combine(tomorrow, datetime.min.time(), tzinfo=UTC)


class RateLimitError(Exception):
    """Rate limit exceeded error"""

    pass


class TokenLimitError(Exception):
    """Token limit exceeded error"""

    pass


class BotAccessDeniedError(Exception):
    """User doesn't have access to this bot version"""

    pass


# FastAPI Router for AgentMax Bot
class AgentMaxBotRouter:
    """FastAPI router for AgentMax Bot endpoints"""

    def __init__(self, bot: AgentMaxBot):
        self.bot = bot

    def get_router(self) -> APIRouter:
        from fastapi import APIRouter, Body, HTTPException, Query

        router = APIRouter(prefix="/AgentMax-bot", tags=["AgentMax Bot"])

        @router.post("/conversations")
        async def create_conversation(
            user_id: str = Body(..., embed=True),
            title: str | None = Body(None, embed=True),
            plan: str = Body(..., embed=True),
        ):
            """Create a new conversation with AgentMax Bot"""

            version = self.bot.get_available_version_for_plan(plan)

            if version == AgentMaxBotVersion.NONE:
                raise HTTPException(
                    status_code=403, detail="AgentMax Bot requires Pro plan or higher"
                )

            conversation = await self.bot.create_conversation(user_id=user_id, title=title)

            return {
                "conversation_id": conversation.conversation_id,
                "version": version.value,
                "created_at": conversation.created_at.isoformat(),
            }

        @router.post("/messages")
        async def send_message(
            request: BotRequest,
            user_id: str = Body(..., embed=True),
            plan: str = Body(..., embed=True),
        ):
            """Send a message to AgentMax Bot"""

            version = self.bot.get_available_version_for_plan(plan)

            if version == AgentMaxBotVersion.NONE:
                raise HTTPException(
                    status_code=403, detail="AgentMax Bot requires Pro plan or higher"
                )

            if request.version and request.version.value > version.value:
                raise HTTPException(
                    status_code=403,
                    detail=f"Plan {plan} doesn't have access to {request.version.value} version",
                )

            try:
                response = await self.bot.send_message(user_id, request)

                return {
                    "response_id": response.response_id,
                    "conversation_id": response.conversation_id,
                    "content": response.content,
                    "model": response.model_version,
                    "usage": response.usage,
                    "timing_ms": response.timing_ms,
                }

            except RateLimitError as e:
                raise HTTPException(status_code=429, detail=str(e)) from e
            except TokenLimitError as e:
                raise HTTPException(status_code=429, detail=str(e)) from e

        @router.get("/conversations/{conversation_id}")
        async def get_conversation(conversation_id: str):
            """Get a specific conversation"""

            conversation = await self.bot.get_conversation(conversation_id)

            if not conversation:
                raise HTTPException(status_code=404, detail="Conversation not found")

            return {
                "conversation_id": conversation.conversation_id,
                "title": conversation.title,
                "message_count": conversation.message_count,
                "total_tokens": conversation.total_tokens,
                "created_at": conversation.created_at.isoformat(),
                "last_message_at": conversation.last_message_at.isoformat(),
                "messages": [
                    {"role": m.role, "content": m.content, "timestamp": m.timestamp.isoformat()}
                    for m in conversation.messages[-10:]
                ],
            }

        @router.get("/conversations")
        async def list_conversations(user_id: str = Query(...), limit: int = Query(50, le=100)):
            """List user's conversations"""

            conversations = await self.bot.list_conversations(user_id, limit)

            return {
                "conversations": [
                    {
                        "conversation_id": c.conversation_id,
                        "title": c.title,
                        "message_count": c.message_count,
                        "last_message_at": c.last_message_at.isoformat(),
                    }
                    for c in conversations
                ]
            }

        @router.get("/usage")
        async def get_usage(user_id: str = Query(...)):
            """Get user's AgentMax Bot usage"""

            usage = await self.bot.get_user_usage(user_id)

            return usage

        @router.get("/capabilities")
        async def get_capabilities(plan: str = Query(...)):
            """Get capabilities available for a plan"""

            version = self.bot.get_available_version_for_plan(plan)

            if version == AgentMaxBotVersion.NONE:
                return {"available": False, "message": "AgentMax Bot requires Pro plan or higher"}

            config = BOT_CONFIGS.get(version)

            return {
                "available": True,
                "version": version.value,
                "name": config.name,
                "description": config.description,
                "capabilities": [cap.value for cap in config.capabilities],
                "daily_limit": config.daily_token_limit,
                "context_window": config.context_window,
            }

        return router
