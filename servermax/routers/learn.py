"""Learn router — lesson upload and retrieval."""

from __future__ import annotations

from fastapi import APIRouter

from servermax.models.schemas import LessonUpload

router = APIRouter(prefix="/learn")

# Central aggregated lesson store (keyed by agent_id → list of lessons).
# In a production deployment this would be backed by a real DB.
_MAX_LESSONS = 50_000
_lessons: list[dict] = []


@router.post("/upload")
async def upload_lessons(body: LessonUpload) -> dict:
    for lesson in body.lessons:
        # Copy so we never mutate the caller-provided request body in place.
        _lessons.append({**lesson, "_agent_id": body.agent_id})
    # Bound memory growth: keep only the most recent lessons.
    if len(_lessons) > _MAX_LESSONS:
        del _lessons[: len(_lessons) - _MAX_LESSONS]
    return {"ok": True, "received": len(body.lessons), "total": len(_lessons)}


@router.get("/lessons")
async def list_lessons(
    limit: int = 50,
    agent_id: str = "",
    context: str = "",
) -> list[dict]:
    limit = max(1, min(limit, _MAX_LESSONS))
    filtered = _lessons
    if agent_id:
        filtered = [x for x in filtered if x.get("_agent_id") == agent_id]
    if context:
        filtered = [x for x in filtered if x.get("context") == context]
    return filtered[-limit:]


@router.get("/stats")
async def stats() -> dict:
    total = len(_lessons)
    unresolved = sum(1 for x in _lessons if not x.get("resolved", False))
    by_agent: dict[str, int] = {}
    for lesson in _lessons:
        aid = lesson.get("_agent_id", "unknown")
        by_agent[aid] = by_agent.get(aid, 0) + 1
    return {"total": total, "unresolved": unresolved, "by_agent": by_agent}
