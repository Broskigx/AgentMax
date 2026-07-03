"""
/v1 runtime discovery endpoints for the AgentMax desktop beta.

These routes are intentionally small and non-sensitive. They let the desktop UI
poll for connectors and pending approvals without hitting the generic 404 path
or leaking local secrets.
"""

from __future__ import annotations

from datetime import UTC, datetime

from fastapi import APIRouter

router = APIRouter(tags=["runtime"])


def _now_iso() -> str:
    return datetime.now(tz=UTC).isoformat()


@router.get("/v1/connectors")
async def list_connectors() -> dict:
    connectors = [
        {
            "id": "license-api",
            "name": "AgentMax License API",
            "status": "online",
            "capabilities": ["auth", "license", "updates", "telemetry_ingest"],
            "risk_level": "low",
        },
        {
            "id": "AgentMax-runtime",
            "name": "AgentMax Runtime",
            "status": "external",
            "capabilities": ["chat", "tools", "vision", "approvals"],
            "risk_level": "medium",
            "note": "La disponibilidad real se valida desde la app local.",
        },
        {
            "id": "desktop-tools",
            "name": "Tauri Desktop Tools",
            "status": "requires_desktop_ipc",
            "capabilities": ["screenshot", "mouse", "keyboard", "shell_supervised"],
            "risk_level": "high",
            "note": "No se ejecutan desde este backend; requieren permiso local.",
        },
    ]
    return {
        "object": "list",
        "count": len(connectors),
        "data": connectors,
        "connectors": connectors,
        "generated_at": _now_iso(),
    }


@router.get("/v1/approvals/pending")
async def pending_approvals() -> dict:
    return {
        "object": "list",
        "count": 0,
        "data": [],
        "approvals": [],
        "status": "ok",
        "message": "No hay aprobaciones pendientes registradas en el backend.",
        "generated_at": _now_iso(),
    }
