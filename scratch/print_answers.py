import json
import sys
from pathlib import Path

repo_root = Path(__file__).resolve().parents[1]
results_file = next((repo_root / "results").glob("*.jsonl"), None)
eval_file = repo_root / "eval" / "public_eval.jsonl"
eval_items = {json.loads(l)["id"]: json.loads(l) for l in eval_file.read_text(encoding="utf-8").splitlines() if l.strip()}

# Let's inspect answer log if saved
answer_log = repo_root / "results" / "eval_out.json"
if answer_log.exists():
    data = json.loads(answer_log.read_text(encoding="utf-8"))
    for item in data.get("details", []):
        if item.get("score", 1.0) < 0.9:
            print(f"[{item['id']}] Q: {eval_items[item['id']]['question']}")
            print(f"  Got: {item.get('text')}")
            print(f"  Missed: {item.get('missed')}")
            print()
