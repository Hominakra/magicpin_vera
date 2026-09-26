from __future__ import annotations

from datetime import datetime
from typing import Any, Optional

from vera.compose import compose
from vera.state import ContextStore, Conversation, ConversationStore, parse_iso


MAX_ACTIONS = 20


def _expired(trigger: dict[str, Any], now: datetime) -> bool:
    exp = parse_iso(trigger.get("expires_at"))
    if exp is None:
        return False
    try:
        return now > exp
    except TypeError:
        return False


def _merchant_paused(convos: ConversationStore, merchant_id: Optional[str], now: datetime) -> bool:
    if not merchant_id:
        return False
    until = convos.merchant_suppressed_until.get(merchant_id)
    if until is None:
        return False
    return now < until


def plan_tick(
    store: ContextStore,
    convos: ConversationStore,
    now: datetime,
    available_triggers: list[str],
) -> list[dict[str, Any]]:
    candidates: list[tuple[int, str, dict[str, Any], dict[str, Any], dict[str, Any], Optional[dict[str, Any]]]] = []

    for trg_id in available_triggers or []:
        trigger = store.payload("trigger", trg_id)
        if not trigger:
            continue
        if _expired(trigger, now):
            continue
        suppression = trigger.get("suppression_key") or ""
        if suppression and suppression in convos.used_suppression:
            continue
        if trg_id in convos.open_for_trigger:
            continue

        merchant_id = trigger.get("merchant_id")
        if _merchant_paused(convos, merchant_id, now):
            continue
        merchant = store.payload("merchant", merchant_id)
        if not merchant:
            continue

        slug = merchant.get("category_slug") or (trigger.get("payload") or {}).get("category")
        category = store.payload("category", slug)
        if not category:
            continue

        customer_id = trigger.get("customer_id")
        customer = store.payload("customer", customer_id) if customer_id else None
        if trigger.get("scope") == "customer" and customer_id and not customer:
            continue

        urgency = int(trigger.get("urgency") or 1)
        candidates.append((urgency, trg_id, category, merchant, trigger, customer))

    candidates.sort(key=lambda x: (-x[0], x[1]))
    actions: list[dict[str, Any]] = []
    seen_merchant: set[str] = set()

    for urgency, trg_id, category, merchant, trigger, customer in candidates:
        if len(actions) >= MAX_ACTIONS:
            break
        merchant_id = trigger.get("merchant_id")
        # One new conversation per merchant per tick (FAQ: one action per merchant+conv)
        if merchant_id in seen_merchant:
            continue
        composed = compose(category, merchant, trigger, customer)
        if not composed.get("body"):
            continue
        customer_id = trigger.get("customer_id")
        conv_id = f"conv_{merchant_id}_{trg_id}"
        conv = Conversation(
            conversation_id=conv_id,
            merchant_id=merchant_id,
            customer_id=customer_id,
            trigger_id=trg_id,
            send_as=composed["send_as"],
            last_topic=trigger.get("kind") or "",
        )
        conv.bodies_sent.append(composed["body"])
        convos.register_outbound(conv, trg_id, composed.get("suppression_key") or "")
        seen_merchant.add(merchant_id)
        actions.append({
            "conversation_id": conv_id,
            "merchant_id": merchant_id,
            "customer_id": customer_id,
            "send_as": composed["send_as"],
            "trigger_id": trg_id,
            "template_name": composed.get("template_name") or "vera_generic_v1",
            "template_params": composed.get("template_params") or [],
            "body": composed["body"],
            "cta": composed["cta"],
            "suppression_key": composed.get("suppression_key") or "",
            "rationale": composed.get("rationale") or "",
        })
    return actions
