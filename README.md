# Vera — magicpin merchant WhatsApp assistant

HTTP bot for the magicpin AI Challenge. The judge pushes category / merchant / customer / trigger context; the bot starts conversations on `/v1/tick` and continues them on `/v1/reply`.

## Run

```bash
pip install -r requirements.txt
uvicorn bot:app --host 0.0.0.0 --port 8080
```

Optional LLM polish (temperature 0). Templates still work with no key:

```bash
set VERA_LLM_PROVIDER=openai
set VERA_LLM_API_KEY=sk-...
set VERA_LLM_MODEL=gpt-4o-mini
```

Local judge (needs its own LLM key inside `judge_simulator.py`):

```bash
set BOT_URL=http://localhost:8080
python judge_simulator.py
```

Contract tests (no LLM required):

```bash
python -m pytest tests/test_bot_contract.py -q
```

## Approach

- **Store**: in-memory, versioned by `(scope, context_id)`. Higher version replaces; stale version returns 409.
- **Compose**: kind-routed templates that only echo numbers/names/citations from the four contexts. Optional LLM rewrite from a fact pack. No URLs. Category taboos stripped.
- **Tick**: skip expired / already-suppressed / missing contexts; rank by urgency; at most one new conversation per merchant per tick (max 20).
- **Reply**: classifiers first — auto-reply, opt-out, commitment, off-topic — then a short on-thread send. `send` / `wait` / `end`.

## Endpoints

| Method | Path | Role |
|---|---|---|
| GET | `/v1/healthz` | liveness + context counts |
| GET | `/v1/metadata` | team / model |
| POST | `/v1/context` | context push |
| POST | `/v1/tick` | proactive sends |
| POST | `/v1/reply` | merchant/customer turn |
| POST | `/v1/teardown` | wipe state after a test |

## Tradeoffs

Templates guarantee latency and no hallucination when the LLM is down. LLM polish can improve voice but is capped at ~7s and falls back. Generated dataset triggers with placeholder payloads are answered with only on-file fields.

Additional context that would have helped: real open appointment slots on every merchant, and non-placeholder payloads on generated triggers.
