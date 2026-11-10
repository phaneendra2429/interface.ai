import json
import sys
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

repo_root = Path(__file__).resolve().parents[1]
pq_file = repo_root / "results" / "per_question.jsonl"
eval_file = repo_root / "eval" / "public_eval.jsonl"
eval_items = {json.loads(l)["id"]: json.loads(l) for l in eval_file.read_text(encoding="utf-8").splitlines() if l.strip()}

for line in pq_file.read_text(encoding="utf-8").splitlines():
    if not line.strip():
        continue
    item = json.loads(line)
    if item.get("score", 1.0) < 0.9:
        q_id = item["id"]
        q_text = eval_items[q_id]["question"]
        print(f"[{q_id}] Q: {q_text}")
        print(f"  Score: {item.get('score')}  Missed: {item.get('missed')}")
        print(f"  Got answer text: {item.get('text')}")
        print(f"  Sources: {item.get('sources')}")
        print()
