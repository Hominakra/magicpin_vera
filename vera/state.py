from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Optional


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def parse_iso(value: Optional[str]) -> Optional[datetime]:
    if not value:
        return None
    try:
        text = value.replace("Z", "+00:00")
        dt = datetime.fromisoformat(text)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt
    except (TypeError, ValueError):
        return None


@dataclass
class StoredContext:
    scope: str
    context_id: str
    version: int
    payload: dict[str, Any]
    delivered_at: str


class ContextStore:
    """In-memory store keyed by (scope, context_id)."""

    VALID_SCOPES = {"category", "merchant", "customer", "trigger"}

    def __init__(self) -> None:
        self._items: dict[tuple[str, str], StoredContext] = {}

    def counts(self) -> dict[str, int]:
        out = {"category": 0, "merchant": 0, "customer": 0, "trigger": 0}
        for (scope, _), _ in self._items.items():
            if scope in out:
                out[scope] += 1
        return out

    def get(self, scope: str, context_id: str) -> Optional[StoredContext]:
        return self._items.get((scope, context_id))

    def payload(self, scope: str, context_id: Optional[str]) -> Optional[dict[str, Any]]:
        if not context_id:
            return None
        item = self.get(scope, context_id)
        return item.payload if item else None

    def put(
        self, scope: str, context_id: str, version: int, payload: dict[str, Any], delivered_at: str
    ) -> tuple[bool, Optional[str], Optional[int]]:
        """Returns (accepted, reason, current_version)."""
        if scope not in self.VALID_SCOPES:
            return False, "invalid_scope", None
        if not context_id:
            return False, "invalid_context_id", None
        if not isinstance(version, int) or version < 1:
            return False, "invalid_version", None
        if not isinstance(payload, dict):
            return False, "invalid_payload", None

        key = (scope, context_id)
        cur = self._items.get(key)
        if cur and cur.version >= version:
            return False, "stale_version", cur.version

        self._items[key] = StoredContext(
            scope=scope,
            context_id=context_id,
            version=version,
            payload=payload,
            delivered_at=delivered_at,
        )
        return True, None, version

    def clear(self) -> None:
        self._items.clear()


@dataclass
class Conversation:
    conversation_id: str
    merchant_id: Optional[str] = None
    customer_id: Optional[str] = None
    trigger_id: Optional[str] = None
    send_as: str = "vera"
    turns: list[dict[str, Any]] = field(default_factory=list)
    bodies_sent: list[str] = field(default_factory=list)
    auto_reply_count: int = 0
    ended: bool = False
    waiting_until: Optional[datetime] = None
    last_topic: str = ""


class ConversationStore:
    def __init__(self) -> None:
        self._items: dict[str, Conversation] = {}
        self.used_suppression: set[str] = set()
        self.open_for_trigger: dict[str, str] = {}  # trigger_id -> conversation_id
        self.merchant_auto_streak: dict[str, int] = {}
        self.merchant_suppressed_until: dict[str, datetime] = {}

    def get(self, conversation_id: str) -> Optional[Conversation]:
        return self._items.get(conversation_id)

    def get_or_create(
        self,
        conversation_id: str,
        merchant_id: Optional[str] = None,
        customer_id: Optional[str] = None,
        trigger_id: Optional[str] = None,
        send_as: str = "vera",
    ) -> Conversation:
        conv = self._items.get(conversation_id)
        if conv is None:
            conv = Conversation(
                conversation_id=conversation_id,
                merchant_id=merchant_id,
                customer_id=customer_id,
                trigger_id=trigger_id,
                send_as=send_as,
            )
            self._items[conversation_id] = conv
        else:
            if merchant_id:
                conv.merchant_id = merchant_id
            if customer_id:
                conv.customer_id = customer_id
        return conv

    def register_outbound(self, conv: Conversation, trigger_id: Optional[str], suppression_key: str) -> None:
        self._items[conv.conversation_id] = conv
        if trigger_id:
            self.open_for_trigger[trigger_id] = conv.conversation_id
        if suppression_key:
            self.used_suppression.add(suppression_key)

    def clear(self) -> None:
        self._items.clear()
        self.used_suppression.clear()
        self.open_for_trigger.clear()
        self.merchant_auto_streak.clear()
        self.merchant_suppressed_until.clear()
