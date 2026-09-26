import json
import urllib.request as urlrequest
from pathlib import Path

def post(path: str, data: dict):
    req = urlrequest.Request(f"http://localhost:8080{path}", data=json.dumps(data).encode("utf-8"), headers={"Content-Type": "application/json"})
    with urlrequest.urlopen(req) as resp:
        return json.loads(resp.read().decode("utf-8"))

def main():
    print("Generating submission.jsonl...")
    post("/v1/teardown", {})

    base = Path("dataset")
    
    # Load all contexts
    for p in (base / "categories").glob("*.json"):
        with open(p, "r", encoding="utf-8") as f:
            data = json.load(f)
            post("/v1/context", {"scope": "category", "context_id": data.get("slug", p.stem), "version": 1, "payload": data})

    with open(base / "customers_seed.json", "r", encoding="utf-8") as f:
        data = json.load(f)
        for c in data.get("customers", []):
            post("/v1/context", {"scope": "customer", "context_id": c["customer_id"], "version": 1, "payload": c})

    with open(base / "merchants_seed.json", "r", encoding="utf-8") as f:
        data = json.load(f)
        for m in data.get("merchants", []):
            post("/v1/context", {"scope": "merchant", "context_id": m["merchant_id"], "version": 1, "payload": m})

    triggers = {}
    with open(base / "triggers_seed.json", "r", encoding="utf-8") as f:
        data = json.load(f)
        for t in data.get("triggers", []):
            triggers[t["id"]] = t
            post("/v1/context", {"scope": "trigger", "context_id": t["id"], "version": 1, "payload": t})

    # Evaluate triggers
    tids = list(triggers.keys())
    results = []
    
    for i in range(0, len(tids), 5):
        batch = tids[i:i+5]
        # We use a date from the dataset period so triggers aren't expired
        data = post("/v1/tick", {"now": "2026-04-25T00:00:00Z", "available_triggers": batch})
        for action in data.get("actions", []):
            results.append({
                "test_id": f"T{len(results)+1:02d}",
                "body": action.get("body", ""),
                "cta": action.get("cta", ""),
                "send_as": action.get("send_as", ""),
                "suppression_key": action.get("suppression_key", ""),
                "rationale": action.get("rationale", "")
            })

    with open("submission.jsonl", "w", encoding="utf-8") as f:
        for r in results:
            f.write(json.dumps(r) + "\n")
            
    print(f"Generated submission.jsonl with {len(results)} lines.")

if __name__ == "__main__":
    main()
