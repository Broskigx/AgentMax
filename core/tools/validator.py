"""Tool input validation with a small JSON-schema subset."""

from __future__ import annotations

import math
from typing import Any

from core.tools.models import ToolDefinition, ToolExecutionContext, ToolRequest, ValidationReport


class ToolValidator:
    """Validate parameters, context, dependencies and execution constraints."""

    def validate(
        self,
        definition: ToolDefinition,
        request: ToolRequest,
        context: ToolExecutionContext,
    ) -> ValidationReport:
        report = ValidationReport.ok_report()
        if not definition.enabled:
            report.add("tool.disabled", f"Tool {definition.id} is disabled")

        self._validate_object_schema(definition.input_schema, request.input, report)
        self._validate_rules(definition, request.input, context, report)

        if definition.dependency_map and not request.dry_run:
            for dep in definition.dependency_map:
                if dep not in context.agent_pool and not getattr(context, dep, None):
                    report.add("tool.dependency_missing", f"Missing dependency: {dep}", dep)
        return report

    def _validate_object_schema(
        self,
        schema: dict[str, Any],
        payload: dict[str, Any],
        report: ValidationReport,
    ) -> None:
        if schema.get("type", "object") != "object":
            report.add("schema.unsupported", "Only object input schemas are supported")
            return
        for name in schema.get("required", []):
            if name not in payload:
                report.add("schema.required", f"Missing required field: {name}", name)
        props = schema.get("properties", {})
        for key, value in payload.items():
            if key not in props:
                if schema.get("additionalProperties", True) is False:
                    report.add("schema.additional", f"Unexpected field: {key}", key)
                continue
            self._validate_value(key, value, props[key], report)

    def _validate_value(
        self,
        field: str,
        value: Any,
        schema: dict[str, Any],
        report: ValidationReport,
    ) -> None:
        expected = schema.get("type")
        if expected == "integer":
            if not isinstance(value, int) or isinstance(value, bool):
                report.add("schema.type", f"{field} must be an integer", field)
                return
        elif expected == "number":
            if (
                not isinstance(value, (int, float))
                or isinstance(value, bool)
                or not math.isfinite(float(value))
            ):
                report.add("schema.type", f"{field} must be a finite number", field)
                return
        elif expected == "string":
            if not isinstance(value, str):
                report.add("schema.type", f"{field} must be a string", field)
                return
        elif expected == "boolean":
            if not isinstance(value, bool):
                report.add("schema.type", f"{field} must be a boolean", field)
                return
        elif expected == "array":
            if not isinstance(value, list):
                report.add("schema.type", f"{field} must be an array", field)
                return
        elif expected == "object":
            if not isinstance(value, dict):
                report.add("schema.type", f"{field} must be an object", field)
                return

        if "minimum" in schema and value < schema["minimum"]:
            report.add("schema.minimum", f"{field} below minimum {schema['minimum']}", field)
        if "maximum" in schema and value > schema["maximum"]:
            report.add("schema.maximum", f"{field} above maximum {schema['maximum']}", field)
        if (
            "maxLength" in schema
            and isinstance(value, str)
            and len(value) > int(schema["maxLength"])
        ):
            report.add(
                "schema.max_length", f"{field} exceeds maxLength {schema['maxLength']}", field
            )
        if "enum" in schema and value not in schema["enum"]:
            report.add("schema.enum", f"{field} must be one of {schema['enum']}", field)

    def _validate_rules(
        self,
        definition: ToolDefinition,
        payload: dict[str, Any],
        context: ToolExecutionContext,
        report: ValidationReport,
    ) -> None:
        rules = set(definition.validation_rules)
        if "coordinates_inside_screen" in rules:
            width, height = context.screen_size
            x = payload.get("x")
            y = payload.get("y")
            if isinstance(x, int) and not 0 <= x < width:
                report.add("screen.x_out_of_bounds", f"x={x} outside width {width}", "x")
            if isinstance(y, int) and not 0 <= y < height:
                report.add("screen.y_out_of_bounds", f"y={y} outside height {height}", "y")
        if "user_is_idle" in rules or definition.requires_user_idle:
            if not context.user_idle:
                report.add("input.user_not_idle", "User input is active; tool must wait")
        if "agent_has_input_control" in rules:
            if context.extra.get("input_control") is False:
                report.add("input.no_control", "Agent does not have input control")
        if "safe_shell_command" in rules:
            command = str(payload.get("command", ""))
            if not command.strip():
                report.add("shell.empty", "Shell command cannot be empty", "command")
        if "path_present" in rules:
            if not str(payload.get("path", "")).strip():
                report.add("path.empty", "Path cannot be empty", "path")
