"""
Workflow Synthesizer -- converts a raw recording into a semantic, robust TaskPlan.

The key insight: we don't replay pixel coordinates.  Instead we send the
AI a human-readable transcript of what the user did (window titles, element
names, typed text) and ask it to generate a plan using the agent's own
vocabulary -- semantic element searches, expected_outcome checks, wait steps
for animations, and critical/non-critical annotations.

The result is an agent workflow that can:
  - Handle the element moving to a different part of the screen
  - Recover from unexpected error dialogs
  - Re-try steps that don't produce the expected visual change
"""

from __future__ import annotations

import json
from typing import Any

import structlog

from core.agents.supervisor import TaskPlan
from core.recorder.event_recorder import RecordedEvent

log = structlog.get_logger(__name__)

SYNTHESIS_SYSTEM_PROMPT = """\
You are an expert RPA (Robotic Process Automation) analyst.
You receive a transcript of actions a user performed manually and must convert
them into a robust, semantic AgentMax workflow plan.

Rules:
- NEVER use raw pixel coordinates in "target" -- use descriptive element names
  (e.g. "Submit button", "Email input field", "Logout menu item").
- Add expected_outcome to EVERY step so the agent can verify success visually.
- Insert "wait" steps (duration_sec: 0.5-2.0) after navigation, form submission,
  or any action that triggers a page/view change.
- Mark steps as critical: false if the element might not appear in every run.
- Group rapid consecutive clicks on the same window into one "click" step.
- Combine typed text from consecutive type events on the same field.
- Add a final "screenshot" step to capture the end state for the audit log.

Return ONLY valid JSON -- no markdown, no explanation.
"""

_STEP_SCHEMA = """\
{
  "steps": [
    {
      "type": "click|type|key|scroll|wait|screenshot|navigate",
      "description": "Short human-readable description",
      "target": "semantic element name or empty string",
      "value": "text to type, key name, scroll direction, or wait duration_sec",
      "critical": true,
      "expected_outcome": "What should appear/change after this step"
    }
  ],
  "risk_level": "low|medium|high|critical",
  "estimated_duration_sec": 10.0,
  "requires_confirmation": false,
  "reasoning": "One sentence explaining the workflow intent"
}
"""


class WorkflowSynthesizer:
    """Converts EventRecorder output into a replayable TaskPlan."""

    def __init__(self, config: Any) -> None:
        from core.ai.ai_router import AIRouter

        self._ai = AIRouter(config)

    async def synthesize(
        self,
        events: list[RecordedEvent],
        screenshots: list[str],
        task_name: str = "Recorded Task",
    ) -> TaskPlan:
        transcript = self._build_transcript(events)
        first_b64 = screenshots[0] if screenshots else ""
        last_b64 = screenshots[-1] if (screenshots and len(screenshots) > 1) else ""

        prompt = (
            f"Task name given by user: {task_name}\n\n"
            f"Recorded user actions (ordered by time):\n{transcript}\n\n"
            f"Convert this into an AgentMax workflow using this JSON schema:\n{_STEP_SCHEMA}\n\n"
            "Important: the first screenshot shows the starting state of the screen."
            " Use element names and window context from the transcript, not positions."
        )

        # Use the starting screenshot for visual context
        raw = await self._ai.vision_query(
            system=SYNTHESIS_SYSTEM_PROMPT,
            prompt=prompt,
            image_b64=first_b64 or last_b64,
        )

        plan = self._parse_plan(raw, task_name)
        log.info("synthesizer.done", task=task_name, steps=len(plan.steps), risk=plan.risk_level)
        return plan

    def _build_transcript(self, events: list[RecordedEvent]) -> str:
        lines: list[str] = []
        prev_window = ""

        for i, ev in enumerate(events, 1):
            if ev.window_title and ev.window_title != prev_window:
                lines.append(f"  [Switched to window: {ev.window_title!r}]")
                prev_window = ev.window_title

            if ev.type == "click":
                parts = []
                if ev.target_element:
                    parts.append(f"element={ev.target_element!r}")
                if ev.target_text:
                    parts.append(f"text={ev.target_text!r}")
                if ev.position:
                    parts.append(f"at ({ev.position[0]}, {ev.position[1]})")
                lines.append(f"{i:3}. CLICK   {', '.join(parts) or '(unknown)'}")

            elif ev.type == "type":
                safe = ev.value or ""
                lines.append(f"{i:3}. TYPE    {safe!r}")

            elif ev.type == "key":
                lines.append(f"{i:3}. KEY     {ev.value}")

            elif ev.type == "scroll":
                lines.append(f"{i:3}. SCROLL  {ev.value}")

            elif ev.type == "window_change":
                lines.append(f"{i:3}. WINDOW  → {ev.window_title!r}")

        return "\n".join(lines) if lines else "(no events recorded)"

    def _parse_plan(self, raw: str, task_name: str) -> TaskPlan:
        from uuid import uuid4

        task_id = str(uuid4())
        try:
            start = raw.find("{")
            end = raw.rfind("}") + 1
            if start >= 0 and end > start:
                data = json.loads(raw[start:end])
            else:
                raise ValueError("No JSON object in response")
        except (json.JSONDecodeError, ValueError) as exc:
            log.warning("synthesizer.parse_fallback", error=str(exc))
            data = {
                "steps": [
                    {"type": "raw", "description": f"Replay: {task_name}", "critical": False}
                ],
                "risk_level": "medium",
                "estimated_duration_sec": 30.0,
                "requires_confirmation": True,
                "reasoning": "Fallback plan -- could not parse AI response",
            }

        return TaskPlan(
            task_id=task_id,
            steps=data.get("steps", []),
            estimated_duration_sec=float(data.get("estimated_duration_sec", 15.0)),
            risk_level=data.get("risk_level", "medium"),
            requires_confirmation=bool(data.get("requires_confirmation", True)),
        )
