"""Agents router — registration and heartbeat."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request

from servermax.models.schemas import AgentInfo, AgentRegisterRequest, HeartbeatRequest

router = APIRouter(prefix="/agents")


@router.post("/register", response_model=AgentInfo, status_code=201)
async def register(body: AgentRegisterRequest, request: Request) -> AgentInfo:
    return await request.app.state.registry.register(body)


@router.post("/{agent_id}/unregister")
async def unregister(agent_id: str, request: Request) -> dict:
    removed = await request.app.state.registry.unregister(agent_id)
    if not removed:
        raise HTTPException(status_code=404, detail="Agent not found")
    return {"ok": True}


@router.post("/heartbeat")
async def heartbeat(body: HeartbeatRequest, request: Request) -> dict:
    found = await request.app.state.registry.heartbeat(
        body.agent_id, status=body.status, lesson_count=body.lesson_count
    )
    if not found:
        raise HTTPException(status_code=404, detail="Agent not registered")
    return {"ok": True}


@router.get("")
async def list_agents(request: Request, online_only: bool = False) -> list[dict]:
    if online_only:
        return await request.app.state.registry.list_online()
    return await request.app.state.registry.list_all()


@router.get("/{agent_id}")
async def get_agent(agent_id: str, request: Request) -> dict:
    agent = await request.app.state.registry.get(agent_id)
    if agent is None:
        raise HTTPException(status_code=404, detail="Agent not found")
    return agent
