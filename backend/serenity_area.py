"""Serenity memorial area routes.

How to wire this into backend/server.py:

1. Add this import near the other imports:
   from serenity_area import serenity_router

2. Add this before app.include_router(api_router):
   app.include_router(serenity_router)

Routes added:
- GET  /api/serenity
- POST /api/serenity/memories
- DELETE /api/serenity/memories/{memory_id}
"""

from datetime import datetime, timezone
from typing import Any, Dict, List, Optional
from uuid import uuid4

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field


serenity_router = APIRouter(prefix="/api/serenity", tags=["Serenity"])

SERENITY_PROFILE: Dict[str, Any] = {
    "name": "Serenity",
    "subtitle": "Protector. Shadow. Goofy girl. Forever on the Naughty List.",
    "years": "2015 - 2026",
    "dedication": (
        "Serenity was my goofy girl, my protector, and my shadow. "
        "She made me laugh all the time, even when she was just being herself. "
        "I will miss her snoring, her presence, and the way she made home feel guarded and full."
    ),
    "quote": "Gone, but this time, never forgotten.",
    "tags": ["Goofy girl", "Protector", "My shadow", "Naughty List forever"],
}

# Safe fallback storage for local/demo mode. If server.py passes a database later,
# this router can be expanded to use a real collection without changing the frontend.
SERENITY_MEMORIES: List[Dict[str, Any]] = [
    {
        "id": "serenity-first-memory",
        "title": "Her snoring",
        "body": "The sound I never thought I would miss this much. Home was louder, safer, and better with Serenity in it.",
        "date": "2026-07-09",
        "created_at": "2026-07-09T00:00:00+00:00",
    },
    {
        "id": "serenity-naughty-list",
        "title": "The Naughty List",
        "body": "Mrs. Claus wrote another name in her book. Serenity belongs there with us — loved, remembered, and forever part of the story.",
        "date": "2026-07-09",
        "created_at": "2026-07-09T00:00:00+00:00",
    },
]


class SerenityMemoryCreate(BaseModel):
    title: str = Field(..., min_length=1, max_length=120)
    body: str = Field(..., min_length=1, max_length=5000)
    date: Optional[str] = None


@serenity_router.get("")
async def get_serenity_area():
    return {
        "profile": SERENITY_PROFILE,
        "memories": SERENITY_MEMORIES,
        "message": "The Serenity Area is live. Forever loved. Forever on the Naughty List.",
    }


@serenity_router.post("/memories")
async def add_serenity_memory(memory: SerenityMemoryCreate):
    now = datetime.now(timezone.utc).isoformat()
    item = {
        "id": str(uuid4()),
        "title": memory.title.strip(),
        "body": memory.body.strip(),
        "date": memory.date or now[:10],
        "created_at": now,
    }
    SERENITY_MEMORIES.insert(0, item)
    return {"ok": True, "memory": item}


@serenity_router.delete("/memories/{memory_id}")
async def delete_serenity_memory(memory_id: str):
    for idx, item in enumerate(SERENITY_MEMORIES):
        if item.get("id") == memory_id:
            removed = SERENITY_MEMORIES.pop(idx)
            return {"ok": True, "removed": removed}
    raise HTTPException(status_code=404, detail="Serenity memory not found")
