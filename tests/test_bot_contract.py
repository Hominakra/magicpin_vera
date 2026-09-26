from __future__ import annotations

import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

import bot

ROOT = Path(__file__).resolve().parents[1]
DATASET = ROOT / "dataset"


@pytest.fixture()
def client():
    bot.contexts.clear()
    bot.conversations.clear()
    with TestClient(bot.app) as c:
        yield c
    bot.contexts.clear()
    bot.conversations.clear()


def load_json(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))


def category(slug: str) -> dict:
    return load_json(DATASET / "categories" / f"{slug}.json")


def merchants() -> dict:
    data = load_json(DATASET / "merchants_seed.json")
    return {m["merchant_id"]: m for m in data["merchants"]}


def customers() -> dict:
    data = load_json(DATASET / "customers_seed.json")
    return {c["customer_id"]: c for c in data["customers"]}


def triggers() -> dict:
    data = load_json(DATASET / "triggers_seed.json")
    return {t["id"]: t for t in data["triggers"]}


def push(client: TestClient, scope: str, cid: str, payload: dict, version: int = 1):
    return client.post(
        "/v1/context",
        json={
            "scope": scope,
            "context_id": cid,
            "version": version,
            "payload": payload,
            "delivered_at": "2026-04-26T09:45:00Z",
        },
    )


def test_healthz_and_metadata(client: TestClient):
    hz = client.get("/v1/healthz")
    assert hz.status_code == 200
    body = hz.json()
    assert body["status"] == "ok"
    assert body["contexts_loaded"]["category"] == 0
    md = client.get("/v1/metadata")
    assert md.status_code == 200
    assert "team_name" in md.json()


def test_context_versioning(client: TestClient):
    merch = merchants()["m_001_drmeera_dentist_delhi"]
    r1 = push(client, "merchant", merch["merchant_id"], merch, 1)
    assert r1.status_code == 200
    assert r1.json()["accepted"] is True
    r_stale = push(client, "merchant", merch["merchant_id"], merch, 1)
    assert r_stale.status_code == 409
    assert r_stale.json()["reason"] == "stale_version"
    updated = dict(merch)
    updated = json.loads(json.dumps(merch))
    updated["performance"] = dict(merch["performance"])
    updated["performance"]["views"] = 2580
    r2 = push(client, "merchant", merch["merchant_id"], updated, 2)
    assert r2.status_code == 200
    stored = bot.contexts.payload("merchant", merch["merchant_id"])
    assert stored["performance"]["views"] == 2580
    bad = client.post(
        "/v1/context",
        json={
            "scope": "not_a_scope",
            "context_id": "x",
            "version": 1,
            "payload": {},
            "delivered_at": "2026-04-26T09:45:00Z",
        },
    )
    assert bad.status_code == 400


def test_warmup_counts(client: TestClient):
    for slug in ["dentists", "salons", "restaurants", "gyms", "pharmacies"]:
        cat = category(slug)
        assert push(client, "category", slug, cat).status_code == 200
    for mid, m in merchants().items():
        assert push(client, "merchant", mid, m).status_code == 200
    for cid, c in customers().items():
        assert push(client, "customer", cid, c).status_code == 200
    hz = client.get("/v1/healthz").json()["contexts_loaded"]
    assert hz["category"] == 5
    assert hz["merchant"] == 10
    assert hz["customer"] == 15
    assert hz["trigger"] == 0


def test_tick_research_digest(client: TestClient):
    cat = category("dentists")
    merch = merchants()["m_001_drmeera_dentist_delhi"]
    trg = triggers()["trg_001_research_digest_dentists"]
    push(client, "category", "dentists", cat)
    push(client, "merchant", merch["merchant_id"], merch)
    push(client, "trigger", trg["id"], trg)
    resp = client.post(
        "/v1/tick",
        json={"now": "2026-04-26T10:35:00Z", "available_triggers": [trg["id"]]},
    )
    assert resp.status_code == 200
    actions = resp.json()["actions"]
    assert len(actions) == 1
    a = actions[0]
    assert a["send_as"] == "vera"
    assert a["trigger_id"] == trg["id"]
    assert a["conversation_id"]
    assert a["suppression_key"] == trg["suppression_key"]
    assert "http" not in a["body"].lower()
    assert "JIDA" in a["body"] or "fluoride" in a["body"].lower() or "2100" in a["body"]
    assert "Meera" in a["body"]
    # second tick should not spam the same suppression key
    resp2 = client.post(
        "/v1/tick",
        json={"now": "2026-04-26T10:40:00Z", "available_triggers": [trg["id"]]},
    )
    assert resp2.json()["actions"] == []


def test_tick_empty_when_unknown_trigger(client: TestClient):
    resp = client.post(
        "/v1/tick",
        json={"now": "2026-04-26T10:35:00Z", "available_triggers": ["trg_missing"]},
    )
    assert resp.json()["actions"] == []


def test_customer_recall_send_as(client: TestClient):
    cat = category("dentists")
    merch = merchants()["m_001_drmeera_dentist_delhi"]
    cust = customers()["c_001_priya_for_m001"]
    trg = triggers()["trg_003_recall_due_priya"]
    push(client, "category", "dentists", cat)
    push(client, "merchant", merch["merchant_id"], merch)
    push(client, "customer", cust["customer_id"], cust)
    push(client, "trigger", trg["id"], trg)
    actions = client.post(
        "/v1/tick",
        json={"now": "2026-04-26T11:00:00Z", "available_triggers": [trg["id"]]},
    ).json()["actions"]
    assert len(actions) == 1
    assert actions[0]["send_as"] == "merchant_on_behalf"
    assert actions[0]["customer_id"] == cust["customer_id"]
    assert "Priya" in actions[0]["body"]
    assert "299" in actions[0]["body"]


def test_auto_reply_hell(client: TestClient):
    mid = "m_001_drmeera_dentist_delhi"
    auto = "Thank you for contacting Dr. Meera's Dental Clinic! Our team will respond shortly."
    actions = []
    for i in range(1, 5):
        r = client.post(
            "/v1/reply",
            json={
                "conversation_id": f"conv_auto_{i}",
                "merchant_id": mid,
                "customer_id": None,
                "from_role": "merchant",
                "message": auto,
                "received_at": "2026-04-26T10:42:00Z",
                "turn_number": i + 1,
            },
        )
        assert r.status_code == 200
        actions.append(r.json()["action"])
    assert "end" in actions
    assert actions[0] in {"send", "wait"}


def test_intent_transition(client: TestClient):
    merch = merchants()["m_001_drmeera_dentist_delhi"]
    push(client, "merchant", merch["merchant_id"], merch)
    r = client.post(
        "/v1/reply",
        json={
            "conversation_id": "conv_intent_1",
            "merchant_id": merch["merchant_id"],
            "from_role": "merchant",
            "message": "Ok lets do it. Whats next?",
            "received_at": "2026-04-26T10:42:00Z",
            "turn_number": 2,
        },
    )
    assert r.status_code == 200
    body = r.json().get("body", "").lower()
    actioning = ["done", "sending", "draft", "here", "confirm", "proceed", "next"]
    qualifying = ["would you", "do you", "can you tell", "what if", "how about"]
    assert any(w in body for w in actioning)
    assert not any(w in body for w in qualifying)


def test_hostile_end(client: TestClient):
    r = client.post(
        "/v1/reply",
        json={
            "conversation_id": "conv_hostile",
            "merchant_id": "m_001_drmeera_dentist_delhi",
            "from_role": "merchant",
            "message": "Stop messaging me. This is useless spam.",
            "received_at": "2026-04-26T10:42:00Z",
            "turn_number": 2,
        },
    )
    data = r.json()
    assert data["action"] == "end"


def test_off_topic_redirect(client: TestClient):
    merch = merchants()["m_001_drmeera_dentist_delhi"]
    trg = triggers()["trg_001_research_digest_dentists"]
    push(client, "merchant", merch["merchant_id"], merch)
    push(client, "trigger", trg["id"], trg)
    # seed conversation with trigger
    conv = bot.conversations.get_or_create("conv_gst", merch["merchant_id"], trigger_id=trg["id"])
    conv.trigger_id = trg["id"]
    r = client.post(
        "/v1/reply",
        json={
            "conversation_id": "conv_gst",
            "merchant_id": merch["merchant_id"],
            "from_role": "merchant",
            "message": "Btw can you also help me with my GST filing this month?",
            "received_at": "2026-04-26T10:42:00Z",
            "turn_number": 2,
        },
    )
    data = r.json()
    assert data["action"] == "send"
    assert "gst" in data["body"].lower() or "ca" in data["body"].lower()


def test_compose_no_hallucinated_url():
    from vera.compose import compose

    cat = category("dentists")
    merch = merchants()["m_001_drmeera_dentist_delhi"]
    trg = triggers()["trg_001_research_digest_dentists"]
    out = compose(cat, merch, trg, None)
    assert "http" not in out["body"].lower()
    assert out["send_as"] == "vera"
    assert "guaranteed" not in out["body"].lower()
