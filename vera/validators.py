from __future__ import annotations

import re
from typing import Any, Optional

URL_RE = re.compile(r"https?://|www\.", re.I)

TABOO_DEFAULT = ["guaranteed", "100% safe", "completely cure", "miracle", "best in city"]


def strip_urls(body: str) -> str:
    return re.sub(r"https?://\S+|www\.\S+", "", body).strip()


def has_url(body: str) -> bool:
    return bool(URL_RE.search(body or ""))


def contains_taboo(body: str, category: Optional[dict[str, Any]]) -> Optional[str]:
    voice = (category or {}).get("voice") or {}
    taboos = voice.get("vocab_taboo") or voice.get("taboos") or TABOO_DEFAULT
    lower = (body or "").lower()
    for t in taboos:
        phrase = str(t).split("(")[0].strip().lower()
        if phrase and phrase in lower:
            return phrase
    return None


def sanitize_body(body: str, category: Optional[dict[str, Any]] = None) -> str:
    text = (body or "").strip()
    text = strip_urls(text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    taboo = contains_taboo(text, category)
    if taboo:
        text = re.sub(re.escape(taboo), "", text, flags=re.I)
    return text.strip()


def normalize_cta(cta: Optional[str], kind: str = "", customer: bool = False) -> str:
    allowed = {
        "open_ended",
        "binary_yes_no",
        "binary_confirm_cancel",
        "multi_choice_slot",
        "none",
    }
    if cta in allowed:
        return cta
    if customer and kind in {"recall_due", "appointment_tomorrow", "trial_followup"}:
        return "multi_choice_slot"
    if kind in {"curious_ask_due", "research_digest", "cde_opportunity"}:
        return "open_ended"
    return "binary_yes_no"


def normalize_send_as(scope: str, customer: Optional[dict[str, Any]]) -> str:
    if scope == "customer" or customer:
        return "merchant_on_behalf"
    return "vera"
