import json
import sys
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

repo_root = Path(__file__).resolve().parents[1]
results_dir = repo_root / "results"
eval_file = repo_root / "eval" / "public_eval.jsonl"
eval_items = {json.loads(line)["id"]: json.loads(line) for line in eval_file.read_text(encoding="utf-8").splitlines() if line.strip()}

res_file = next(results_dir.glob("*.jsonl"), None)
if res_file:
    for line in res_file.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        d = json.loads(line)
        if d.get("score", 1.0) < 0.9:
            q_id = d["id"]
            eval_item = eval_items.get(q_id, {})
            print(f"ID: {q_id} [{d.get('category')}] Score: {d.get('score')}")
            print(f"  Q: {eval_item.get('question')}")
            print(f"  Expected: {eval_item.get('expected_answer')}")
            print(f"  Must include: {eval_item.get('must_include')}")
            print(f"  Matched: {d.get('matched')}")
            print(f"  Missed: {d.get('missed')}")
            print()
