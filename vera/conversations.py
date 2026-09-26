from __future__ import annotations

from typing import Any, Optional

from vera.state import ConversationStore, ContextStore, utcnow
from vera.validators import sanitize_body

AUTO_REPLY_MARKERS = [
    "thank you for contacting",
    "thanks for contacting",
    "our team will respond shortly",
    "we will get back to you",
    "automated assistant",
    "this is an automated",
    "aapki jaankari ke liye",
    "main ek automated",
]

OPT_OUT_MARKERS = [
    "stop messaging",
    "stop sending",
    "not interested",
    "unsubscribe",
    "don't message",
    "do not message",
    "useless spam",
    "this is useless",
    "bothering me",
    "stop it",
]

COMMIT_MARKERS = [
    "let's do it",
    "lets do it",
    "ok let's",
    "ok lets",
    "go ahead",
    "yes please",
    "yes, send",
    "send me the",
    "what's next",
    "whats next",
    "do it",
    "confirm",
    "i want to join",
    "judrna",
    "join magicpin",
]

OFF_TOPIC_MARKERS = [
    "gst",
    "income tax",
    "itr",
    "file my tax",
    "pan card",
    "loan",
    "credit card",
]


def looks_auto_reply(message: str) -> bool:
    text = (message or "").strip().lower()
    if not text:
        return False
    return any(m in text for m in AUTO_REPLY_MARKERS)


def looks_opt_out(message: str) -> bool:
    text = (message or "").strip().lower()
    return any(m in text for m in OPT_OUT_MARKERS)


def looks_commit(message: str) -> bool:
    text = (message or "").strip().lower()
    return any(m in text for m in COMMIT_MARKERS)


def looks_off_topic(message: str) -> bool:
    text = (message or "").strip().lower()
    return any(m in text for m in OFF_TOPIC_MARKERS)


def handle_reply(
    store: ContextStore,
    convos: ConversationStore,
    conversation_id: str,
    merchant_id: Optional[str],
    customer_id: Optional[str],
    from_role: str,
    message: str,
    turn_number: int,
) -> dict[str, Any]:
    conv = convos.get_or_create(conversation_id, merchant_id, customer_id)
    conv.turns.append({"from": from_role, "msg": message, "turn": turn_number})

    if conv.ended:
        return {"action": "end", "rationale": "Conversation already closed."}

    mid = conv.merchant_id or merchant_id or ""
    text = message or ""

    if looks_opt_out(text):
        conv.ended = True
        if mid:
            convos.merchant_suppressed_until[mid] = utcnow()
        return {
            "action": "end",
            "rationale": "Merchant/customer asked to stop; closing without further prompts.",
        }

    if looks_auto_reply(text):
        conv.auto_reply_count += 1
        streak = convos.merchant_auto_streak.get(mid, 0) + 1
        convos.merchant_auto_streak[mid] = streak
        n = max(conv.auto_reply_count, streak)
        if n == 1:
            body = (
                "Looks like a WhatsApp auto-reply. When the owner is free, reply YES and I'll continue. "
                "I won't keep pinging the canned message."
            )
            if body in conv.bodies_sent:
                return {"action": "wait", "wait_seconds": 14400, "rationale": "Auto-reply; backing off 4h."}
            conv.bodies_sent.append(body)
            return {
                "action": "send",
                "body": body,
                "cta": "binary_yes_no",
                "rationale": "Detected canned auto-reply; one owner-directed prompt, then back off.",
            }
        if n == 2:
            return {
                "action": "wait",
                "wait_seconds": 86400,
                "rationale": "Same auto-reply pattern again; owner not at phone. Wait 24h.",
            }
        conv.ended = True
        return {
            "action": "end",
            "rationale": "Auto-reply 3+ times with no human signal; closing the thread.",
        }

    # Real human text resets auto-reply streak
    if mid:
        convos.merchant_auto_streak[mid] = 0

    merchant = store.payload("merchant", conv.merchant_id or merchant_id) or {}
    trigger = store.payload("trigger", conv.trigger_id) or {}
    category_slug = merchant.get("category_slug") or (trigger.get("payload") or {}).get("category")
    category = store.payload("category", category_slug) or {}
    customer = store.payload("customer", conv.customer_id or customer_id)
    ident = merchant.get("identity") or {}
    owner = ident.get("owner_first_name") or ident.get("name") or "there"
    biz = ident.get("name") or "your listing"

    if looks_off_topic(text) and not looks_commit(text):
        topic = trigger.get("kind") or conv.last_topic or "the earlier note"
        body = (
            f"I'll leave that to your CA / specialist — outside what I can do on WhatsApp. "
            f"Coming back to {topic.replace('_', ' ')} for {biz}: want the draft, or pause here?"
        )
        body = sanitize_body(body, category)
        if body in conv.bodies_sent:
            return {"action": "end", "rationale": "Off-topic already redirected once; stopping to avoid loops."}
        conv.bodies_sent.append(body)
        return {
            "action": "send",
            "body": body,
            "cta": "open_ended",
            "rationale": "Out-of-scope ask declined; redirected to original trigger.",
        }

    if looks_commit(text):
        kind = trigger.get("kind") or "the task"
        offers = [o.get("title") for o in (merchant.get("offers") or []) if o.get("status") == "active"]
        offer_bit = f" Using your live offer {offers[0]}." if offers else ""
        body = (
            f"Great {owner} — moving to action on {kind.replace('_', ' ')}.{offer_bit} "
            f"Drafting now. Reply CONFIRM and I'll send the WhatsApp/GBP copy in this chat "
            f"(no more qualifying questions)."
        )
        body = sanitize_body(body, category)
        if body in conv.bodies_sent:
            body = f"Confirm received. Sending the draft next — {kind.replace('_', ' ')} for {biz}."
        conv.bodies_sent.append(body)
        return {
            "action": "send",
            "body": body,
            "cta": "binary_confirm_cancel",
            "rationale": "Explicit commitment detected; switched from qualifying to execution.",
        }

    # General continuation: stay on trigger, don't re-qualify endlessly
    kind = trigger.get("kind") or "this thread"
    last_q = (text or "").strip()[:180]
    body = (
        f"Noted, {owner}: \"{last_q}\". I'll use that on {kind.replace('_', ' ')} for {biz}. "
        f"Reply YES for a ready-to-send draft, or STOP to pause."
    )
    body = sanitize_body(body, category)
    if body in conv.bodies_sent:
        return {
            "action": "wait",
            "wait_seconds": 1800,
            "rationale": "Would repeat the same follow-up; waiting 30 min instead.",
        }
    conv.bodies_sent.append(body)
    return {
        "action": "send",
        "body": body,
        "cta": "binary_yes_no",
        "rationale": "Acknowledged merchant reply and advanced the original trigger.",
    }
