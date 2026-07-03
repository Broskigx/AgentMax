"""Internal action and tool planning for AgentMax."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

_DANGEROUS_COMMAND_PATTERNS = (
    "format ",
    "diskpart",
    "cipher /w",
    "reg delete",
    "remove-item",
    "del ",
    "rmdir",
    "rd /",
    "shutdown",
    "stop-process",
    "taskkill",
    "takeown",
    "icacls",
)

_RUNTIME_DEPENDENCIES = {
    "ui_automation",
    "file_system",
    "web_search",
    "vision",
    "screen",
    "memory",
    "safety",
}


@dataclass(frozen=True, slots=True)
class RetryPolicy:
    """Retry behavior for one tool type."""

    max_attempts: int = 1
    backoff_ms: int = 0
    retry_on: tuple[str, ...] = ("transient_error",)

    @classmethod
    def from_raw(cls, value: Any) -> RetryPolicy:
        if not isinstance(value, dict):
            return cls()
        retry_on = value.get("retry_on", ("transient_error",))
        if isinstance(retry_on, str):
            retry_on = (retry_on,)
        return cls(
            max_attempts=max(1, min(int(value.get("max_attempts", 1) or 1), 5)),
            backoff_ms=max(0, min(int(value.get("backoff_ms", 0) or 0), 10_000)),
            retry_on=tuple(str(item) for item in retry_on),
        )

    def as_dict(self) -> dict[str, Any]:
        return {
            "max_attempts": self.max_attempts,
            "backoff_ms": self.backoff_ms,
            "retry_on": list(self.retry_on),
        }


@dataclass(frozen=True, slots=True)
class ToolPolicy:
    """Execution policy attached to a step/tool."""

    name: str
    internal_tool_priority: int
    reasoning_weight: float
    risk_score: float
    required_params: tuple[str, ...] = ()
    execution_dependencies: tuple[str, ...] = ()
    fallback_chain: tuple[str, ...] = ()
    dangerous: bool = False
    category: str = "general"
    execution_mode: str = "async"
    timeout_sec: float = 15.0
    confidence_threshold: float = 0.55
    retry_policy: RetryPolicy = field(default_factory=RetryPolicy)
    execution_cost: float = 1.0
    permission_level: str = "standard"
    sandbox_mode: str = "default"
    dependency_map: tuple[str, ...] = ()


@dataclass(slots=True)
class ToolDecision:
    """Private decision for a single tool step."""

    allowed: bool
    reason: str
    normalized_step: dict[str, Any]
    validation_pipeline: list[str] = field(default_factory=list)
    fallback_chain: list[str] = field(default_factory=list)
    risk_score: float = 0.0
    priority: int = 0
    retryable: bool = False
    dependency_blockers: list[str] = field(default_factory=list)
    execution_mode: str = "async"
    timeout_sec: float = 15.0
    confidence_threshold: float = 0.55
    retry_policy: dict[str, Any] = field(default_factory=dict)
    execution_cost: float = 1.0
    permission_level: str = "standard"
    sandbox_mode: str = "default"


class ActionToolPlanner:
    """Validates, annotates and ranks internal tool usage."""

    def __init__(self) -> None:
        self._registry: dict[str, ToolPolicy] = {
            "move_mouse": ToolPolicy(
                "mouse.move",
                72,
                0.74,
                0.30,
                ("x", "y"),
                category="mouse",
                timeout_sec=5,
                permission_level="input",
                retry_policy=RetryPolicy(2, 150),
            ),
            "click": ToolPolicy(
                "ui_automation",
                70,
                0.70,
                0.18,
                fallback_chain=("screenshot",),
                category="ui",
                timeout_sec=8,
                retry_policy=RetryPolicy(2, 250),
            ),
            "type": ToolPolicy(
                "ui_automation",
                70,
                0.72,
                0.22,
                ("value",),
                category="ui",
                timeout_sec=8,
                retry_policy=RetryPolicy(2, 250),
            ),
            "key": ToolPolicy("ui_automation", 65, 0.62, 0.18, category="ui", timeout_sec=5),
            "scroll": ToolPolicy("ui_automation", 50, 0.45, 0.08, category="ui", timeout_sec=5),
            "wait": ToolPolicy("timer", 25, 0.20, 0.02, category="control", timeout_sec=3),
            "screenshot": ToolPolicy("vision", 90, 0.85, 0.04, category="vision", timeout_sec=6),
            "navigate": ToolPolicy(
                "ui_automation",
                58,
                0.55,
                0.25,
                fallback_chain=("screenshot",),
                category="ui",
                timeout_sec=12,
                retry_policy=RetryPolicy(2, 300),
            ),
            # open_app / close_app replaced by vision-based "computer" and "navigate" (no app names)
            "open_app": ToolPolicy(
                "computer",
                74,
                0.78,
                0.45,
                ("target", "description"),
                category="computer",
                timeout_sec=12,
                permission_level="input",
                retry_policy=RetryPolicy(2, 500),
                execution_cost=1.2,
            ),
            "close_app": ToolPolicy(
                "computer",
                62,
                0.72,
                0.45,
                ("target", "description"),
                category="computer",
                execution_mode="sandboxed",
                timeout_sec=12,
                permission_level="input",
                sandbox_mode="restricted",
                retry_policy=RetryPolicy(1, 0),
                execution_cost=1.1,
            ),
            "shell": ToolPolicy(
                "shell",
                45,
                0.80,
                0.62,
                ("command",),
                dangerous=True,
                category="system",
                execution_mode="sandboxed",
                timeout_sec=15,
                permission_level="elevated",
                sandbox_mode="restricted",
                retry_policy=RetryPolicy(1, 0),
                execution_cost=2.0,
            ),
            "read_file": ToolPolicy(
                "file_system", 60, 0.60, 0.18, ("path",), category="file", timeout_sec=8
            ),
            "write_file": ToolPolicy(
                "file_system",
                42,
                0.75,
                0.52,
                ("path", "content"),
                dangerous=True,
                category="file",
                timeout_sec=10,
                permission_level="write",
                sandbox_mode="workspace",
            ),
            "list_dir": ToolPolicy("file_system", 55, 0.40, 0.12, category="file", timeout_sec=8),
            "move_file": ToolPolicy(
                "file_system",
                40,
                0.70,
                0.48,
                ("source", "destination"),
                dangerous=True,
                category="file",
                timeout_sec=10,
                permission_level="write",
                sandbox_mode="workspace",
            ),
            "delete_file": ToolPolicy(
                "file_system",
                15,
                0.90,
                0.88,
                ("path",),
                dangerous=True,
                category="file",
                timeout_sec=10,
                permission_level="destructive",
                sandbox_mode="workspace",
            ),
            "search": ToolPolicy(
                "web_search",
                52,
                0.55,
                0.16,
                ("query",),
                category="network",
                timeout_sec=18,
                retry_policy=RetryPolicy(2, 500),
                execution_cost=1.4,
            ),
            "read_page": ToolPolicy(
                "web_search",
                50,
                0.55,
                0.14,
                ("url",),
                category="network",
                timeout_sec=18,
                retry_policy=RetryPolicy(2, 500),
                execution_cost=1.4,
            ),
            "raw": ToolPolicy(
                "planning",
                20,
                0.40,
                0.35,
                category="planning",
                execution_mode="internal",
                timeout_sec=2,
            ),
        }
        self._load_json_registry()

    @property
    def registry(self) -> dict[str, ToolPolicy]:
        return dict(self._registry)

    def tool_manifest(self) -> list[dict[str, Any]]:
        return [
            {
                "step_type": step_type,
                "tool": policy.name,
                "category": policy.category,
                "execution_mode": policy.execution_mode,
                "internal_tool_priority": policy.internal_tool_priority,
                "reasoning_weight": policy.reasoning_weight,
                "risk_score": policy.risk_score,
                "permission_level": policy.permission_level,
                "sandbox_mode": policy.sandbox_mode,
                "confidence_threshold": policy.confidence_threshold,
                "retry_policy": policy.retry_policy.as_dict(),
                "fallback_chain": list(policy.fallback_chain),
                "validation_pipeline": self._validation_pipeline_for(
                    step_type, policy.risk_score, policy
                ),
                "execution_cost": policy.execution_cost,
                "dependency_map": list(policy.dependency_map or policy.execution_dependencies),
            }
            for step_type, policy in sorted(
                self._registry.items(),
                key=lambda item: item[1].internal_tool_priority,
                reverse=True,
            )
        ]

    def enrich_steps(self, steps: list[dict[str, Any]]) -> list[dict[str, Any]]:
        return [self._annotate_step(step, index) for index, step in enumerate(steps)]

    def decide(self, step: dict[str, Any], *, confirmed: bool = False) -> ToolDecision:
        step_type = str(step.get("type", "raw"))
        policy = self._registry.get(step_type)
        normalized = self._annotate_step(step, int(step.get("_sequence", 0) or 0))
        validation_pipeline = list(normalized["validation_pipeline"])
        fallback_chain = list(normalized["fallback_chain"])
        risk = self._dynamic_risk(normalized, policy)
        normalized["risk_score"] = risk
        retryable = bool(fallback_chain)

        if not policy:
            return ToolDecision(False, f"Unknown tool step type: {step_type}", normalized)

        hard_block = self._hard_block_reason(normalized)
        if hard_block:
            return self._decision(
                False,
                hard_block,
                normalized,
                policy,
                validation_pipeline,
                fallback_chain,
                risk,
                retryable,
            )

        # Resolve field aliases before checking required params.
        # The router maps model-facing names (value, app, keys) to tool-schema
        # names (text, target, keys) AFTER this check runs, so we need to honour
        # the same aliases here to avoid false "missing parameter" rejections.
        check = dict(normalized)
        if "value" in check:
            check.setdefault("text", check["value"])
            check.setdefault("keys", check["value"])
            check.setdefault("key", check["value"])
        if "app" in check:
            check.setdefault("target", check["app"])

        missing = [param for param in policy.required_params if not check.get(param)]
        if missing:
            return self._decision(
                False,
                f"Missing required tool parameter(s): {', '.join(missing)}",
                normalized,
                policy,
                validation_pipeline,
                fallback_chain,
                risk,
                retryable,
            )

        if policy.dangerous and risk >= 0.70 and not confirmed:
            return self._decision(
                False,
                "Dangerous tool requires explicit confirmation",
                normalized,
                policy,
                validation_pipeline,
                fallback_chain,
                risk,
                retryable,
            )

        dependency_blockers = self._dependency_blockers(normalized, policy)
        if dependency_blockers:
            return self._decision(
                False,
                "Missing execution dependencies: " + ", ".join(dependency_blockers),
                normalized,
                policy,
                validation_pipeline,
                fallback_chain,
                risk,
                retryable,
                dependency_blockers,
            )

        return self._decision(
            True,
            "validated",
            normalized,
            policy,
            validation_pipeline,
            fallback_chain,
            risk,
            retryable,
        )

    def fallback_steps(self, step: dict[str, Any]) -> list[dict[str, Any]]:
        fallbacks: list[dict[str, Any]] = []
        for fallback in step.get("fallback_chain", []):
            if fallback == "screenshot":
                fallbacks.append(
                    {"type": "screenshot", "description": "Capture state for recovery"}
                )
            elif fallback == "shell" and step.get("type") == "navigate":
                raw_target = step.get("target") or step.get("value") or ""
                target = (
                    raw_target.get("text", "") if isinstance(raw_target, dict) else str(raw_target)
                )
                fallbacks.append({"type": "shell", "command": f'start "" "{target}"'})
        return self.enrich_steps(fallbacks)

    def _annotate_step(self, step: dict[str, Any], sequence: int) -> dict[str, Any]:
        step_type = str(step.get("type", "raw"))
        policy = self._registry.get(step_type, self._registry["raw"])
        annotated = dict(step)
        annotated.setdefault("_sequence", sequence)
        annotated.setdefault("internal_tool_priority", policy.internal_tool_priority)
        annotated.setdefault("reasoning_weight", policy.reasoning_weight)
        annotated.setdefault("risk_score", policy.risk_score)
        annotated.setdefault("tool_category", policy.category)
        annotated.setdefault("execution_mode", policy.execution_mode)
        annotated.setdefault("timeout_sec", policy.timeout_sec)
        annotated.setdefault("confidence_threshold", policy.confidence_threshold)
        annotated.setdefault("retry_policy", policy.retry_policy.as_dict())
        annotated.setdefault("execution_cost", policy.execution_cost)
        annotated.setdefault("permission_level", policy.permission_level)
        annotated.setdefault("sandbox_mode", policy.sandbox_mode)
        annotated.setdefault("execution_dependencies", list(policy.execution_dependencies))
        annotated.setdefault(
            "dependency_map", list(policy.dependency_map or policy.execution_dependencies)
        )
        annotated.setdefault("fallback_chain", list(policy.fallback_chain))
        annotated.setdefault(
            "validation_pipeline",
            self._validation_pipeline_for(step_type, policy.risk_score, policy),
        )
        return annotated

    def _validation_pipeline_for(
        self,
        step_type: str,
        risk_score: float,
        policy: ToolPolicy | None = None,
    ) -> list[str]:
        pipeline = ["schema", "permissions", "parameter_integrity"]
        if step_type in {
            "move_mouse",
            "click",
            "type",
            "key",
            "scroll",
            "navigate",
            "open_app",
            "close_app",
        }:
            pipeline.append("screen_state")
        if policy and policy.sandbox_mode != "default":
            pipeline.append("sandbox_scope")
        if policy and policy.timeout_sec:
            pipeline.append("timeout_guard")
        if step_type in {"write_file", "move_file", "delete_file", "shell"} or risk_score >= 0.5:
            pipeline.append("confirmation_gate")
        if policy and policy.retry_policy.max_attempts > 1:
            pipeline.append("retry_policy")
        pipeline.append("postcondition")
        return pipeline

    def _dynamic_risk(self, step: dict[str, Any], policy: ToolPolicy | None) -> float:
        risk = float(step.get("risk_score", policy.risk_score if policy else 0.5) or 0.0)
        step_type = str(step.get("type", "raw"))
        if step_type == "shell":
            command = str(step.get("command", "")).lower()
            if any(pattern in command for pattern in _DANGEROUS_COMMAND_PATTERNS):
                risk = max(risk, 0.86)
            if any(token in command for token in ("system32", "registry", "admin")):
                risk = max(risk, 0.74)
            if any(token in command for token in ("invoke-expression", "iex", "downloadstring")):
                risk = max(risk, 0.90)
        if step_type == "delete_file" and self._looks_like_root_or_wildcard(step.get("path")):
            risk = 1.0
        if step_type in {"write_file", "move_file"} and self._looks_like_protected_path(step):
            risk = max(risk, 0.78)
        return min(max(risk, 0.0), 1.0)

    def _hard_block_reason(self, step: dict[str, Any]) -> str | None:
        step_type = str(step.get("type", "raw"))
        if step_type == "delete_file" and self._looks_like_root_or_wildcard(step.get("path")):
            return "Refusing broad or root-level delete target"
        if step_type == "shell":
            command = str(step.get("command", "")).strip().lower()
            if not command:
                return "shell step missing 'command'"
            if " && " in command or " | " in command:
                return "Compound shell commands require decomposition into separate validated steps"
            if ("invoke-expression" in command or "iex" in command) and "http" in command:
                return "Remote script execution is blocked"
        return None

    def _dependency_blockers(self, step: dict[str, Any], policy: ToolPolicy) -> list[str]:
        required = tuple(
            dep
            for dep in (policy.dependency_map or policy.execution_dependencies)
            if dep not in _RUNTIME_DEPENDENCIES
        )
        if not required:
            return []
        completed = step.get("completed_dependencies") or step.get("_completed_dependencies") or []
        completed_set = (
            {str(item) for item in completed} if isinstance(completed, list) else {str(completed)}
        )
        return [dep for dep in required if dep not in completed_set]

    def _decision(
        self,
        allowed: bool,
        reason: str,
        normalized: dict[str, Any],
        policy: ToolPolicy,
        validation_pipeline: list[str],
        fallback_chain: list[str],
        risk: float,
        retryable: bool,
        dependency_blockers: list[str] | None = None,
    ) -> ToolDecision:
        return ToolDecision(
            allowed=allowed,
            reason=reason,
            normalized_step=normalized,
            validation_pipeline=validation_pipeline,
            fallback_chain=fallback_chain,
            risk_score=risk,
            priority=policy.internal_tool_priority,
            retryable=retryable or policy.retry_policy.max_attempts > 1,
            dependency_blockers=dependency_blockers or [],
            execution_mode=policy.execution_mode,
            timeout_sec=policy.timeout_sec,
            confidence_threshold=policy.confidence_threshold,
            retry_policy=policy.retry_policy.as_dict(),
            execution_cost=policy.execution_cost,
            permission_level=policy.permission_level,
            sandbox_mode=policy.sandbox_mode,
        )

    def _load_json_registry(self) -> None:
        path = Path(__file__).with_name("tools.json")
        if not path.exists():
            return
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            return
        tools = raw.get("tools", raw) if isinstance(raw, dict) else raw
        if isinstance(tools, list):
            iterator = (
                (self._step_type_from_tool(policy_data), policy_data)
                for policy_data in tools
                if isinstance(policy_data, dict)
            )
        elif isinstance(tools, dict):
            iterator = tools.items()
        else:
            return
        for step_type, policy_data in iterator:
            if not step_type:
                continue
            if not isinstance(policy_data, dict):
                continue
            try:
                self._registry[str(step_type)] = self._policy_from_json(policy_data)
            except Exception:
                continue

    def _policy_from_json(self, raw: dict[str, Any]) -> ToolPolicy:
        retry_policy = RetryPolicy.from_raw(raw.get("retry_policy"))
        dependencies = self._tuple_mapped(raw.get("execution_dependencies"))
        dependency_map = self._tuple_mapped(raw.get("dependency_map")) or dependencies
        execution_mode = str(raw.get("execution_mode", "async"))
        timeout_sec = raw.get("timeout_sec")
        if timeout_sec is None and raw.get("timeout_ms") is not None:
            timeout_sec = float(raw.get("timeout_ms") or 0) / 1000
        sandbox_mode = raw.get("sandbox_mode")
        if sandbox_mode is None:
            category = str(raw.get("category", ""))
            tool_id = str(raw.get("id", ""))
            if execution_mode == "sandboxed":
                sandbox_mode = "restricted"
            elif category == "filesystem" and any(
                token in tool_id for token in ("write", "move", "delete")
            ):
                sandbox_mode = "workspace"
            else:
                sandbox_mode = "default"
        sandbox_mode = str(sandbox_mode)
        risk_score = raw.get("risk_score")
        if risk_score is None:
            risk_score = self._risk_score_from_level(raw.get("risk_level"))
        permission_level = raw.get("permission_level")
        if permission_level is None:
            permission_level = self._permission_level_from_tool(raw, float(risk_score or 0.35))
        return ToolPolicy(
            name=str(raw.get("id", raw.get("name", raw.get("tool", "planning")))),
            internal_tool_priority=int(
                raw.get("internal_tool_priority", raw.get("priority", 20)) or 20
            ),
            reasoning_weight=float(raw.get("reasoning_weight", 0.4) or 0.4),
            risk_score=float(risk_score or 0.35),
            required_params=self._required_params_from_json(raw),
            execution_dependencies=dependencies,
            fallback_chain=self._tuple_mapped(raw.get("fallback_chain")),
            dangerous=bool(raw.get("dangerous", False)) or float(risk_score or 0.35) >= 0.5,
            category=str(raw.get("category", "general")),
            execution_mode=execution_mode,
            timeout_sec=float(timeout_sec or 15.0),
            confidence_threshold=float(raw.get("confidence_threshold", 0.55) or 0.55),
            retry_policy=retry_policy,
            execution_cost=float(raw.get("execution_cost", 1.0) or 1.0),
            permission_level=str(permission_level),
            sandbox_mode=sandbox_mode,
            dependency_map=dependency_map,
        )

    def _step_type_from_tool(self, raw: dict[str, Any]) -> str | None:
        explicit = raw.get("step_type")
        if explicit:
            return str(explicit)
        tool_id = str(raw.get("id", ""))
        mapping = {
            "mouse.move": "move_mouse",
            "mouse.click": "click",
            "mouse.scroll": "scroll",
            "keyboard.type_text": "type",
            "keyboard.hotkey": "key",
            "keyboard.press": "key",
            "screen.screenshot": "screenshot",
            # Legacy app names removed; use vision-based "computer" or "navigate" with visual target
            # legacy app.open/app.close removed - use "computer" tool for OpenAI-style vision + mouse actions only.
            "shell.run": "shell",
            "filesystem.read": "read_file",
            "filesystem.write": "write_file",
            "filesystem.list": "list_dir",
            "filesystem.move": "move_file",
            "filesystem.delete": "delete_file",
            "browser.search": "search",
            "browser.read_page": "read_page",
            "task.wait": "wait",
            "reasoning.raw": "raw",
        }
        return mapping.get(tool_id)

    def _required_params_from_json(self, raw: dict[str, Any]) -> tuple[str, ...]:
        explicit = self._tuple(raw.get("required_params"))
        if explicit:
            return explicit
        schema = raw.get("input_schema")
        if isinstance(schema, dict):
            return self._tuple(schema.get("required"))
        return ()

    def _risk_score_from_level(self, level: Any) -> float:
        return {
            "none": 0.02,
            "low": 0.15,
            "medium": 0.45,
            "high": 0.82,
            "critical": 0.95,
        }.get(str(level or "").lower(), 0.35)

    def _permission_level_from_tool(self, raw: dict[str, Any], risk_score: float) -> str:
        tool_id = str(raw.get("id", ""))
        category = str(raw.get("category", ""))
        if "delete" in tool_id:
            return "destructive"
        if risk_score >= 0.85:
            return "destructive"
        if tool_id == "shell.run":
            return "elevated"
        if category == "filesystem" and any(
            token in tool_id for token in ("write", "move", "delete")
        ):
            return "write"
        if category in {"mouse", "keyboard", "window", "app"}:
            return "input"
        if category == "browser":
            return "network"
        if category == "filesystem":
            return "read"
        return "standard"

    def _tuple_mapped(self, value: Any) -> tuple[str, ...]:
        return tuple(self._step_type_from_tool({"id": item}) or item for item in self._tuple(value))

    def _tuple(self, value: Any) -> tuple[str, ...]:
        if value is None:
            return ()
        if isinstance(value, str):
            return (value,)
        if isinstance(value, dict):
            return tuple(str(item) for item in value.keys())
        if isinstance(value, (list, tuple, set)):
            return tuple(str(item) for item in value)
        return (str(value),)

    def _looks_like_root_or_wildcard(self, path: Any) -> bool:
        if path is None:
            return True
        text = str(path).strip().strip('"').strip("'").lower()
        return text in {"", ".", "/", "\\", "c:", "c:\\", "*", "*.*"} or text.endswith("\\*")

    def _looks_like_protected_path(self, step: dict[str, Any]) -> bool:
        values = [step.get("path"), step.get("source"), step.get("destination")]
        lowered = " ".join(str(value).lower() for value in values if value)
        return any(
            token in lowered for token in ("\\windows\\", "\\system32\\", "\\program files\\")
        )
