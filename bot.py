from __future__ import annotations

import os
import time
from datetime import datetime, timezone
from typing import Any, Optional

from fastapi import FastAPI
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from vera.conversations import handle_reply
from vera.state import ContextStore, ConversationStore
from vera.tick import plan_tick

app = FastAPI(title="Vera Merchant Assistant", version="1.0.0")
START = time.time()
contexts = ContextStore()
conversations = ConversationStore()


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


@app.get("/v1/healthz")
async def healthz():
    return {
        "status": "ok",
        "uptime_seconds": int(time.time() - START),
        "contexts_loaded": contexts.counts(),
    }


@app.get("/v1/metadata")
async def metadata():
    return {
        "team_name": os.getenv("VERA_TEAM_NAME", "magicpin_vera"),
        "team_members": [m.strip() for m in os.getenv("VERA_TEAM_MEMBERS", "homin").split(",") if m.strip()],
        "model": os.getenv("VERA_LLM_MODEL") or os.getenv("VERA_LLM_PROVIDER") or "template_composer_v1",
        "approach": (
            "kind-routed deterministic composer over the 4-context pack, "
            "optional temperature-0 LLM polish, classifier-first reply policy"
        ),
        "contact_email": os.getenv("VERA_CONTACT_EMAIL", "team@example.com"),
        "version": "1.0.0",
        "submitted_at": os.getenv("VERA_SUBMITTED_AT", "2026-04-26T08:00:00Z"),
    }


class CtxBody(BaseModel):
    scope: str
    context_id: str
    version: int
    payload: dict[str, Any]
    delivered_at: str = Field(default_factory=_now_iso)


@app.post("/v1/context")
async def push_context(body: CtxBody):
    accepted, reason, current = contexts.put(
        body.scope, body.context_id, body.version, body.payload, body.delivered_at
    )
    if not accepted and reason == "stale_version":
        return JSONResponse(
            status_code=409,
            content={"accepted": False, "reason": "stale_version", "current_version": current},
        )
    if not accepted:
        return JSONResponse(
            status_code=400,
            content={"accepted": False, "reason": reason or "invalid_scope", "details": reason},
        )
    return {
        "accepted": True,
        "ack_id": f"ack_{body.context_id}_v{body.version}",
        "stored_at": _now_iso(),
    }


class TickBody(BaseModel):
    now: str
    available_triggers: list[str] = []


@app.post("/v1/tick")
async def tick(body: TickBody):
    now = datetime.now(timezone.utc)
    try:
        raw = body.now.replace("Z", "+00:00")
        parsed = datetime.fromisoformat(raw)
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        now = parsed
    except (TypeError, ValueError):
        pass
    actions = plan_tick(contexts, conversations, now, body.available_triggers)
    return {"actions": actions}


class ReplyBody(BaseModel):
    conversation_id: str
    merchant_id: Optional[str] = None
    customer_id: Optional[str] = None
    from_role: str
    message: str
    received_at: str
    turn_number: int


@app.post("/v1/reply")
async def reply(body: ReplyBody):
    return handle_reply(
        contexts,
        conversations,
        conversation_id=body.conversation_id,
        merchant_id=body.merchant_id,
        customer_id=body.customer_id,
        from_role=body.from_role,
        message=body.message,
        turn_number=body.turn_number,
    )


class ComposeBody(BaseModel):
    category: dict[str, Any]
    merchant: dict[str, Any]
    trigger: dict[str, Any]
    customer: Optional[dict[str, Any]] = None


@app.post("/v1/compose")
async def compose_endpoint(body: ComposeBody):
    """Helper for local scoring of compose() without the tick scheduler."""
    from vera.compose import compose

    return compose(body.category, body.merchant, body.trigger, body.customer)


@app.post("/v1/teardown")
async def teardown():
    contexts.clear()
    conversations.clear()
    return {"ok": True}


def compose(category: dict, merchant: dict, trigger: dict, customer: dict | None = None) -> dict:
    """Challenge-brief entrypoint used by tests and submission generation."""
    from vera.compose import compose as _compose

    return _compose(category, merchant, trigger, customer)
