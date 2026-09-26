from __future__ import annotations

import json
from typing import Any, Optional

from vera import llm
from vera.validators import normalize_cta, normalize_send_as, sanitize_body


def _ident(merchant: dict[str, Any]) -> dict[str, Any]:
    return merchant.get("identity") or {}


def _owner(merchant: dict[str, Any]) -> str:
    ident = _ident(merchant)
    return ident.get("owner_first_name") or ident.get("name") or "there"


def _biz(merchant: dict[str, Any]) -> str:
    return _ident(merchant).get("name") or "your business"


def _locality(merchant: dict[str, Any]) -> str:
    ident = _ident(merchant)
    loc = ident.get("locality") or ""
    city = ident.get("city") or ""
    if loc and city:
        return f"{loc}, {city}"
    return loc or city


def _langs(merchant: dict[str, Any], customer: Optional[dict[str, Any]]) -> str:
    if customer:
        return (customer.get("identity") or {}).get("language_pref") or "en"
    langs = _ident(merchant).get("languages") or ["en"]
    if "hi" in langs:
        return "hi-en mix"
    return "en"


def _mix(pref: str) -> bool:
    p = (pref or "").lower()
    return "hi" in p or "mix" in p or "hindi" in p


def _active_offers(merchant: dict[str, Any]) -> list[str]:
    out = []
    for o in merchant.get("offers") or []:
        if (o.get("status") or "").lower() == "active" and o.get("title"):
            out.append(o["title"])
    return out


def _peer(category: dict[str, Any]) -> dict[str, Any]:
    return category.get("peer_stats") or {}


def _digest_item(category: dict[str, Any], item_id: Optional[str]) -> Optional[dict[str, Any]]:
    if not item_id:
        return None
    for item in category.get("digest") or []:
        if item.get("id") == item_id:
            return item
    return None


def _first_digest(category: dict[str, Any], kind: Optional[str] = None) -> Optional[dict[str, Any]]:
    items = category.get("digest") or []
    if kind:
        for item in items:
            if item.get("kind") == kind:
                return item
    return items[0] if items else None


def _perf(merchant: dict[str, Any]) -> dict[str, Any]:
    return merchant.get("performance") or {}


def _pct(value: Any) -> str:
    try:
        n = float(value)
        if abs(n) <= 1.5:
            return f"{n * 100:.0f}%"
        return f"{n:.0f}%"
    except (TypeError, ValueError):
        return str(value)


def _fact_pack(
    category: dict[str, Any],
    merchant: dict[str, Any],
    trigger: dict[str, Any],
    customer: Optional[dict[str, Any]],
) -> dict[str, Any]:
    ident = _ident(merchant)
    agg = merchant.get("customer_aggregate") or {}
    payload = trigger.get("payload") or {}
    cust_id = customer.get("identity") if customer else None
    rel = customer.get("relationship") if customer else None
    return {
        "category_slug": category.get("slug"),
        "voice": category.get("voice"),
        "peer_stats": _peer(category),
        "digest": category.get("digest"),
        "seasonal_beats": category.get("seasonal_beats"),
        "offer_catalog": [o.get("title") for o in (category.get("offer_catalog") or [])[:6]],
        "merchant_name": ident.get("name"),
        "owner_first_name": ident.get("owner_first_name"),
        "locality": ident.get("locality"),
        "city": ident.get("city"),
        "languages": ident.get("languages"),
        "verified": ident.get("verified"),
        "subscription": merchant.get("subscription"),
        "performance": _perf(merchant),
        "offers": merchant.get("offers"),
        "signals": merchant.get("signals"),
        "customer_aggregate": agg,
        "review_themes": merchant.get("review_themes"),
        "conversation_history": merchant.get("conversation_history"),
        "trigger_kind": trigger.get("kind"),
        "trigger_scope": trigger.get("scope"),
        "trigger_urgency": trigger.get("urgency"),
        "trigger_payload": payload,
        "suppression_key": trigger.get("suppression_key"),
        "customer_name": (cust_id or {}).get("name") if cust_id else None,
        "customer_language": (cust_id or {}).get("language_pref") if cust_id else None,
        "customer_state": customer.get("state") if customer else None,
        "customer_relationship": rel,
        "customer_preferences": customer.get("preferences") if customer else None,
        "consent": customer.get("consent") if customer else None,
    }


def _hi_cta_yes(mix: bool) -> str:
    return "Reply YES if you want me to handle it." if not mix else "Reply YES — main draft kar deti hoon."


def compose_template(
    category: dict[str, Any],
    merchant: dict[str, Any],
    trigger: dict[str, Any],
    customer: Optional[dict[str, Any]] = None,
) -> dict[str, Any]:
    kind = trigger.get("kind") or "generic"
    payload = trigger.get("payload") or {}
    scope = trigger.get("scope") or ("customer" if customer else "merchant")
    mix = _mix(_langs(merchant, customer))
    owner = _owner(merchant)
    biz = _biz(merchant)
    loc = _locality(merchant)
    offers = _active_offers(merchant)
    perf = _perf(merchant)
    agg = merchant.get("customer_aggregate") or {}
    peer = _peer(category)
    signals = merchant.get("signals") or []

    body = ""
    cta = "open_ended"
    rationale = ""
    template_name = f"vera_{kind}_v1"
    params: list[str] = [owner]

    if kind == "research_digest":
        item = _digest_item(category, payload.get("top_item_id")) or _first_digest(category, "research")
        item = item or {}
        n = item.get("trial_n")
        trial = f"{n}-patient trial" if n else "new trial"
        title = item.get("title") or "this week's research note"
        source = item.get("source") or "category digest"
        summary = item.get("summary") or title
        cohort = agg.get("high_risk_adult_count")
        cohort_bit = f" for your {cohort} high-risk adult patients" if cohort else ""
        body = (
            f"{owner}, a digest item just landed{cohort_bit}: {summary} "
            f"({trial}. Source: {source}). I can pull the 2-min abstract and draft a patient note you can forward. "
            f"{_hi_cta_yes(mix)}"
        )
        cta = "open_ended"
        rationale = f"Research digest {item.get('id')} cited from category context; merchant cohort used if present."
        params = [owner, str(summary)[:180], source]

    elif kind == "regulation_change":
        item = _digest_item(category, payload.get("top_item_id")) or _first_digest(category, "compliance")
        item = item or {}
        deadline = payload.get("deadline_iso") or item.get("date") or ""
        body = (
            f"{owner}, compliance note from {item.get('source') or 'your category digest'}: "
            f"{item.get('title') or 'regulation update'}. "
            f"{item.get('summary') or item.get('actionable') or ''} "
            f"{('Deadline: ' + str(deadline) + '. ') if deadline else ''}"
            f"Want me to turn this into a 5-line SOP checklist for {biz}?"
        )
        cta = "binary_yes_no"
        rationale = "Regulation trigger with source + deadline from payload/digest; offer a checklist artifact."
        params = [owner, item.get("title") or "regulation update", str(deadline)]

    elif kind == "cde_opportunity":
        item = _digest_item(category, payload.get("digest_item_id")) or _first_digest(category, "cde")
        item = item or {}
        credits = payload.get("credits") or item.get("credits")
        when = item.get("date") or ""
        fee = payload.get("fee") or item.get("actionable") or ""
        body = (
            f"{owner}, CDE slot: {item.get('title') or 'upcoming webinar'}"
            f"{(' on ' + when) if when else ''}"
            f"{(f' — {credits} credits') if credits else ''}. {fee}. "
            f"Want me to hold a reminder and draft a 1-line GBP post after you attend?"
        )
        cta = "binary_yes_no"
        rationale = "CDE opportunity from digest; low-friction reminder + GBP post offer."
        params = [owner, item.get("title") or "CDE", str(credits or "")]

    elif kind == "recall_due" and customer:
        name = (customer.get("identity") or {}).get("name") or "there"
        slots = payload.get("available_slots") or []
        slot_txt = " / ".join(s.get("label") for s in slots if s.get("label")) or "a weekday evening slot"
        due = payload.get("due_date") or payload.get("service_due") or "your recall"
        last = payload.get("last_service_date") or (customer.get("relationship") or {}).get("last_visit") or ""
        price = offers[0] if offers else None
        price_bit = f" {price}." if price else ""
        if mix:
            body = (
                f"Hi {name}, {biz} here. Last visit {last} — {due} is due. "
                f"Apke liye slots: {slot_txt}.{price_bit} "
                f"Reply 1 for the first slot, 2 for the second, or send a time that works."
            )
        else:
            body = (
                f"Hi {name}, {biz} here. Last visit {last} — {due} is due. "
                f"Open slots: {slot_txt}.{price_bit} "
                f"Reply 1 for the first slot, 2 for the second, or send a time that works."
            )
        cta = "multi_choice_slot"
        rationale = "Customer recall using real slots + merchant active offer; language pref honored."
        template_name = "merchant_recall_reminder_v1"
        params = [name, biz, str(last), slot_txt, price or ""]

    elif kind in {"customer_lapsed_hard", "customer_lapsed_soft"} and customer:
        name = (customer.get("identity") or {}).get("name") or "there"
        days = payload.get("days_since_last_visit")
        focus = payload.get("previous_focus") or ""
        offer = offers[0] if offers else ""
        gap = f"{days} days" if days else "a while"
        focus_bit = f" Your last focus was {focus.replace('_', ' ')}." if focus else ""
        offer_bit = f" Current offer: {offer}." if offer else ""
        body = (
            f"Hi {name} — {owner} from {biz} here. It's been {gap}.{focus_bit}{offer_bit} "
            f"Want me to hold a no-commitment trial/return slot next week? Reply YES — no auto-charge."
        )
        cta = "binary_yes_no"
        template_name = "merchant_winback_v1"
        rationale = "Lapse winback with days + prior focus from trigger; no-shame framing."
        params = [name, owner, biz, str(days or "")]

    elif kind == "chronic_refill_due" and customer:
        name = (customer.get("identity") or {}).get("name") or "there"
        mols = payload.get("molecule_list") or []
        mol_txt = ", ".join(mols) if mols else "your regular medicines"
        when = payload.get("stock_runs_out_iso") or ""
        date_bit = when[:10] if when else "soon"
        sub = merchant.get("subscription") or {}
        offer_bit = f" Active: {offers[0]}." if offers else ""
        body = (
            f"Namaste — {biz} ({loc}) here. {name} ki medicines ({mol_txt}) run out {date_bit}. "
            f"Same pack can be prepared.{offer_bit} Reply CONFIRM to dispatch, or tell us if the dose changed."
        )
        cta = "binary_confirm_cancel"
        template_name = "merchant_refill_v1"
        rationale = "Chronic refill with molecule list and run-out date from payload; pharmacy voice."
        params = [name, biz, mol_txt, date_bit]

    elif kind in {"wedding_package_followup", "trial_followup"} and customer:
        name = (customer.get("identity") or {}).get("name") or "there"
        wedding = payload.get("wedding_date") or (customer.get("preferences") or {}).get("wedding_date")
        days = payload.get("days_to_wedding")
        window = payload.get("next_step_window_open") or payload.get("trial_date") or ""
        slots = payload.get("next_session_options") or []
        slot = slots[0].get("label") if slots else ""
        offer = offers[0] if offers else ""
        days_bit = f"{days} days to the wedding. " if days else ""
        date_bit = f"Wedding date {wedding}. " if wedding else ""
        slot_bit = f" I can hold {slot}." if slot else ""
        body = (
            f"Hi {name}, {owner} from {biz} here. {days_bit}{date_bit}"
            f"Next step window: {window or 'follow-up session'}.{slot_bit} "
            f"{('Package reference: ' + offer + '. ') if offer else ''}"
            f"Reply YES to block it, or send another time."
        )
        cta = "binary_yes_no"
        template_name = "merchant_followup_v1"
        rationale = "Follow-up using wedding/trial payload dates; merchant first name + offer if present."
        params = [name, owner, str(days or wedding or "")]

    elif kind == "appointment_tomorrow" and customer:
        name = (customer.get("identity") or {}).get("name") or "there"
        when = payload.get("slot_label") or payload.get("iso") or "tomorrow"
        body = (
            f"Hi {name}, {biz} here — reminder for {when}. "
            f"Reply YES to confirm, or send a new time."
        )
        cta = "binary_yes_no"
        template_name = "merchant_appt_v1"
        rationale = "Appointment reminder from trigger payload."
        params = [name, biz, str(when)]

    elif kind == "perf_dip":
        metric = payload.get("metric") or "calls"
        delta = payload.get("delta_pct")
        baseline = payload.get("vs_baseline")
        window = payload.get("window") or "7d"
        ctr = perf.get("ctr")
        peer_ctr = peer.get("avg_ctr")
        peer_bit = ""
        if ctr is not None and peer_ctr:
            peer_bit = f" Your CTR is {ctr} vs peer {peer_ctr}."
        body = (
            f"{owner}, {metric} dropped {_pct(delta)} over {window}"
            f"{(' vs baseline ' + str(baseline)) if baseline is not None else ''} at {biz}.{peer_bit} "
            f"I've drafted a 3-line Google post + a service-at-price line from your catalog. "
            f"Reply YES and I'll drop both in one message."
        )
        cta = "binary_yes_no"
        rationale = "Perf dip uses trigger delta and peer CTR from category; effort externalized."
        params = [owner, metric, _pct(delta)]

    elif kind == "seasonal_perf_dip":
        metric = payload.get("metric") or "views"
        delta = payload.get("delta_pct")
        note = payload.get("season_note") or "seasonal lull"
        members = agg.get("total_unique_ytd") or agg.get("active_members")
        member_bit = f" Keep focus on the {members} people already in roster." if members else ""
        body = (
            f"{owner}, {metric} are {_pct(delta)} this week — flagged as expected ({note}), not a listing failure. "
            f"Skip extra ad spend in this window.{member_bit} "
            f"Want a short retention challenge draft for existing members?"
        )
        cta = "binary_yes_no"
        rationale = "Seasonal dip reframed using is_expected_seasonal payload; roster count if present."
        params = [owner, _pct(delta), note]

    elif kind == "perf_spike":
        metric = payload.get("metric") or "views"
        delta = payload.get("delta_pct")
        driver = payload.get("likely_driver") or "recent activity"
        body = (
            f"{owner}, {metric} are up {_pct(delta)} ({payload.get('window') or '7d'}) — likely {driver}. "
            f"Want me to lock in a follow-up Google post so the spike doesn't go stale?"
        )
        cta = "binary_yes_no"
        rationale = "Perf spike with driver from payload; convert attention into a post."
        params = [owner, metric, _pct(delta)]

    elif kind == "milestone_reached":
        metric = payload.get("metric") or "reviews"
        now = payload.get("value_now")
        target = payload.get("milestone_value")
        imminent = payload.get("is_imminent")
        gap = None
        if now is not None and target is not None:
            try:
                gap = int(target) - int(now)
            except (TypeError, ValueError):
                gap = None
        if imminent and gap is not None:
            body = (
                f"{owner}, {biz} is at {now} {metric} — {gap} short of {target}. "
                f"Want 4 one-line review asks you can send after today's happy customers?"
            )
        else:
            body = (
                f"{owner}, milestone: {now} {metric} (target {target}). "
                f"Want me to draft a thank-you Google post you can publish today?"
            )
        cta = "binary_yes_no"
        rationale = "Milestone uses exact counts from trigger; ask for a concrete artifact."
        params = [owner, str(now), str(target)]

    elif kind == "renewal_due":
        days = payload.get("days_remaining") or (merchant.get("subscription") or {}).get("days_remaining")
        plan = payload.get("plan") or (merchant.get("subscription") or {}).get("plan")
        amount = payload.get("renewal_amount")
        amount_bit = f" Amount on file: ₹{amount}." if amount else ""
        body = (
            f"{owner}, {plan or 'Pro'} for {biz} has {days} days left.{amount_bit} "
            f"If you want continuity, reply YES and I'll start renewal — no extra qualifying questions."
        )
        cta = "binary_yes_no"
        rationale = "Renewal uses days_remaining and amount from payload; immediate action CTA."
        params = [owner, str(days), str(plan or "")]

    elif kind == "winback_eligible":
        days = payload.get("days_since_expiry")
        dip = payload.get("perf_dip_pct")
        lapsed = payload.get("lapsed_customers_added_since_expiry")
        body = (
            f"{owner}, {biz} plan lapsed {days} days ago"
            f"{(' and calls/views are ' + _pct(dip)) if dip is not None else ''}. "
            f"{(str(lapsed) + ' extra lapsed customers since expiry. ') if lapsed is not None else ''}"
            f"I can restart with one service-at-price offer from your category catalog. Reply YES to draft it."
        )
        cta = "binary_yes_no"
        rationale = "Winback uses expiry days + lapsed customer count from payload."
        params = [owner, str(days), str(lapsed or "")]

    elif kind == "festival_upcoming":
        fest = payload.get("festival") or "the festival"
        date = payload.get("date") or ""
        days = payload.get("days_until")
        offer = offers[0] if offers else (category.get("offer_catalog") or [{}])[0].get("title")
        body = (
            f"{owner}, {fest} is {date}"
            f"{(' (' + str(days) + ' days)') if days is not None else ''}. "
            f"{('Lead with ' + offer + ' rather than a flat discount. ') if offer else ''}"
            f"Want a 3-line WhatsApp + GBP post I can queue?"
        )
        cta = "binary_yes_no"
        rationale = "Festival trigger with date; service+price from merchant/catalog, not a % off."
        params = [owner, fest, str(date)]

    elif kind == "ipl_match_today":
        match = payload.get("match") or "tonight's match"
        venue = payload.get("venue") or loc
        time_iso = payload.get("match_time_iso") or ""
        weeknight = payload.get("is_weeknight")
        offer = offers[0] if offers else ""
        if weeknight is False:
            body = (
                f"{owner} — {match} at {venue}"
                f"{(' (' + time_iso[11:16] + ')') if len(time_iso) >= 16 else ''}. "
                f"Saturday match nights usually pull covers to home viewing. "
                f"Skip a match-crowd promo; push {offer or 'your current delivery offer'} as delivery-only. "
                f"Want the Swiggy line + a story caption in 10 min?"
            )
        else:
            body = (
                f"{owner} — {match} at {venue} tonight. "
                f"{('Your ' + offer + ' can be the match-night line. ') if offer else ''}"
                f"Want me to draft the delivery caption now?"
            )
        cta = "binary_yes_no"
        rationale = "IPL trigger uses match/venue/weeknight from payload and existing merchant offer."
        params = [owner, match, venue]

    elif kind == "review_theme_emerged":
        theme = payload.get("theme") or "a repeating theme"
        n = payload.get("occurrences_30d")
        quote = payload.get("common_quote")
        trend = payload.get("trend")
        quote_bit = f' Customer quote: "{quote}".' if quote else ""
        body = (
            f"{owner}, {n} reviews in 30d mention {theme.replace('_', ' ')}"
            f"{(' (' + trend + ')') if trend else ''}.{quote_bit} "
            f"I can draft a public reply template + a 1-line ops fix you can pin for staff. Reply YES."
        )
        cta = "binary_yes_no"
        rationale = "Review theme uses count + quote from trigger; offer reply + ops artifact."
        params = [owner, str(n), theme]

    elif kind == "curious_ask_due":
        ask = payload.get("ask_template") or "what_service_in_demand_this_week"
        body = (
            f"Hi {owner} — quick check for {biz}: which service got the most asks this week? "
            f"I'll turn your answer into a Google post + a 4-line price reply you can paste. About 5 min."
        )
        cta = "open_ended"
        rationale = "Curious-ask family: ask the merchant (lever #7) with reciprocity."
        params = [owner, biz, ask]

    elif kind == "active_planning_intent":
        topic = (payload.get("intent_topic") or "the plan").replace("_", " ")
        last = payload.get("merchant_last_message") or ""
        catalog = [o.get("title") for o in (category.get("offer_catalog") or [])[:3] if o.get("title")]
        catalog_bit = f" Catalog anchors: {', '.join(catalog)}." if catalog else ""
        body = (
            f"{owner}, you said you want to move on {topic}"
            f"{(' — \"' + last + '\"') if last else ''}. Here's a starter you can edit:\n"
            f"- Locality: {loc}\n"
            f"- Lead with service+price, not a blanket discount.{catalog_bit}\n"
            f"Want me to draft the customer WhatsApp and a 3-tier price card next?"
        )
        cta = "binary_yes_no"
        rationale = "Planning intent: merchant already committed; deliver a starter artifact, no extra qualification."
        params = [owner, topic, loc]

    elif kind == "supply_alert":
        mol = payload.get("molecule") or "stock"
        batches = payload.get("affected_batches") or []
        mfr = payload.get("manufacturer") or ""
        batch_txt = ", ".join(batches) if batches else "listed batches"
        chronic = agg.get("total_unique_ytd")
        body = (
            f"{owner}, urgent supply alert on {mol}"
            f"{(' from ' + mfr) if mfr else ''}: batches {batch_txt}. "
            f"{('You have ' + str(chronic) + ' unique customers on file to cross-check. ') if chronic else ''}"
            f"Want a customer WhatsApp + replacement-pickup script?"
        )
        cta = "binary_yes_no"
        rationale = "Supply alert uses batch numbers from payload; roster size from merchant aggregate."
        params = [owner, mol, batch_txt]

    elif kind == "category_seasonal":
        season = payload.get("season") or "this season"
        trends = payload.get("trends") or []
        trend_txt = ", ".join(str(t) for t in trends[:4])
        body = (
            f"{owner}, {season} demand shift for {biz}: {trend_txt}. "
            f"Want a 4-line shelf/plan note you can share with staff today?"
        )
        cta = "binary_yes_no"
        rationale = "Seasonal category trends listed from payload without adding extra numbers."
        params = [owner, season, trend_txt]

    elif kind == "gbp_unverified":
        path = payload.get("verification_path") or "postcard or phone"
        uplift = payload.get("estimated_uplift_pct")
        uplift_bit = f" Listings that verify typically see about {_pct(uplift)} more discovery." if uplift is not None else ""
        body = (
            f"{owner}, Google still shows {biz} as unverified. Path: {path}.{uplift_bit} "
            f"Reply YES and I'll walk the steps in one message — no extra qualification."
        )
        cta = "binary_yes_no"
        rationale = "Unverified GBP from merchant/trigger; action handoff on YES."
        params = [owner, biz, path]

    elif kind == "competitor_opened":
        name = payload.get("competitor_name") or "a nearby listing"
        dist = payload.get("distance_km")
        their = payload.get("their_offer")
        opened = payload.get("opened_date")
        ours = offers[0] if offers else None
        body = (
            f"{owner}, {name} opened"
            f"{(' ' + str(dist) + ' km away') if dist is not None else ''}"
            f"{(' on ' + opened) if opened else ''}"
            f"{(' with ' + their) if their else ''}. "
            f"{('Your live offer is ' + ours + '. ') if ours else ''}"
            f"Want a side-by-side of your photos/hours vs theirs — I can draft it, you just glance?"
        )
        cta = "binary_yes_no"
        rationale = "Competitor payload only; compare to this merchant's active offer, curiosity CTA."
        params = [owner, name, str(dist or "")]

    elif kind == "dormant_with_vera":
        days = payload.get("days_since_last_merchant_message")
        topic = payload.get("last_topic") or "last thread"
        body = (
            f"Hi {owner} — it's been {days} days since {biz} last replied (last topic: {topic}). "
            f"One useful item from this week, no pitch: want a 3-line profile check, or shall I pause?"
        )
        cta = "open_ended"
        rationale = "Dormancy re-entry with last topic; easy pause option."
        params = [owner, str(days), topic]

    else:
        # Placeholder / unknown kinds: stay factual, no invention.
        views = perf.get("views")
        calls = perf.get("calls")
        offer_bit = f" Live offer: {offers[0]}." if offers else ""
        payload_bits = []
        for k, v in list(payload.items())[:6]:
            if v is True or v in (None, "", "placeholder"):
                continue
            payload_bits.append(f"{k}={v}")
        extra = ("; ".join(payload_bits)) if payload_bits else "using only the fields on file"
        body = (
            f"{owner}, pinging because of {kind.replace('_', ' ')} at {biz} ({loc}). "
            f"{('30d views ' + str(views) + ', calls ' + str(calls) + '. ') if views is not None else ''}"
            f"{offer_bit} Trigger detail: {extra}. "
            f"Want me to draft the next step, or STOP if this isn't useful?"
        )
        cta = "open_ended"
        rationale = f"Generic kind={kind}; only echoed payload/merchant fields, no invented facts."
        params = [owner, kind, extra[:120]]

    if any(s.startswith("stale_posts") for s in signals) and kind in {"curious_ask_due", "research_digest"}:
        body = body.rstrip() + " (Also: Google posts look stale vs a ~14-day peer cadence.)"

    send_as = normalize_send_as(scope, customer)
    body = sanitize_body(body, category)
    cta = normalize_cta(cta, kind, customer is not None)
    suppression = trigger.get("suppression_key") or f"{kind}:{merchant.get('merchant_id')}"

    return {
        "body": body,
        "cta": cta,
        "send_as": send_as,
        "suppression_key": suppression,
        "rationale": rationale,
        "template_name": template_name,
        "template_params": [p for p in params if p is not None],
    }


COMPOSE_SYSTEM = """You are magicpin Vera, a hyper-personalized WhatsApp assistant for merchants. Write ONE concise message based on FACTS_JSON and TEMPLATE_DRAFT.

Strict Rules for 10/10 Performance:
1. SPECIFICITY: Use ONLY numbers, dates, prices, and sources from FACTS_JSON. NEVER invent facts, competitors, or URLs.
2. CATEGORY FIT: Match the business type perfectly.
   - Dentists: MUST use "Dr. [Owner Name]", clinical, peer-to-peer tone.
   - Salons: Warm, friendly, practical.
   - Restaurants: Operator-to-operator.
   - Gyms: Coaching, motivational.
   - Pharmacies: Trustworthy, precise.
   - Check voice.tone and NEVER use taboo words from voice.vocab_taboo.
3. MERCHANT FIT: MUST address the owner by their first name (or Dr. Name). Reference their specific locality, live offers, and metrics. If languages include "hi", write in a natural Hindi-English mix (Hinglish).
4. TRIGGER RELEVANCE (WHY NOW?): Explicitly connect the trigger_kind and payload to WHY you are messaging right now. Explain the immediate value or risk.
5. ENGAGEMENT COMPULSION: Make it impossible to ignore. Use loss aversion, curiosity, or social proof. End with a crystal clear, low-friction CTA as the final sentence (e.g. "Reply YES", "Reply 1 for X, 2 for Y").

Return JSON only: {"body": "...", "cta": "open_ended|binary_yes_no|binary_confirm_cancel|multi_choice_slot|none", "rationale": "one sentence explaining how you hit all 5 dimensions"}
"""


def _llm_polish(base: dict[str, Any], facts: dict[str, Any], category: dict[str, Any]) -> dict[str, Any]:
    prompt = (
        "FACTS_JSON:\n"
        + json.dumps(facts, ensure_ascii=False, default=str)[:6000]
        + "\n\nTEMPLATE_DRAFT (you may rewrite, keep all facts):\n"
        + json.dumps({"body": base["body"], "cta": base["cta"], "rationale": base["rationale"]}, ensure_ascii=False)
    )
    raw = llm.complete(prompt, COMPOSE_SYSTEM, timeout=25)
    data = llm.extract_json_object(raw or "")
    if not data or not data.get("body"):
        return base
    body = sanitize_body(str(data["body"]), category)
    if not body or len(body) < 20:
        return base
    out = dict(base)
    out["body"] = body
    out["cta"] = normalize_cta(data.get("cta"), str(facts.get("trigger_kind") or ""), bool(facts.get("customer_name")))
    if data.get("rationale"):
        out["rationale"] = str(data["rationale"])[:400]
    return out


def compose(
    category: dict,
    merchant: dict,
    trigger: dict,
    customer: dict | None = None,
) -> dict:
    """Public composer: dict in, dict out. Deterministic templates; optional LLM polish."""
    category = category or {}
    merchant = merchant or {}
    trigger = trigger or {}
    base = compose_template(category, merchant, trigger, customer)
    if llm.llm_enabled():
        facts = _fact_pack(category, merchant, trigger, customer)
        try:
            base = _llm_polish(base, facts, category)
        except Exception:
            pass
    return {
        "body": base["body"],
        "cta": base["cta"],
        "send_as": base["send_as"],
        "suppression_key": base["suppression_key"],
        "rationale": base["rationale"],
        "template_name": base["template_name"],
        "template_params": base["template_params"],
    }
