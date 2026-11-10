import json
import sys
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

repo_root = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(repo_root))
sys.path.insert(0, str(repo_root / "harness"))

from run_eval import load_snapshots
import solution

snaps = load_snapshots(repo_root / "snapshots")
ks = solution.build()
for s in snaps:
    ks.ingest(s)

eval_file = repo_root / "eval" / "public_eval.jsonl"
eval_items = [json.loads(l) for l in eval_file.read_text(encoding="utf-8").splitlines() if l.strip()]

for item in eval_items:
    q = item["question"]
    as_of = item.get("as_of")
    ans = ks.answer(q, as_of=as_of)
    must = item.get("must_include", [])
    missed = [m for m in must if m not in ans.text]
    if missed:
        print(f"[{item['id']}] Q: {q}")
        print(f"  Ans: {ans.text}")
        print(f"  Confident: {ans.confident}")
        print(f"  Missed: {missed}")
        print(f"  Sources: {[s.url for s in ans.sources]}")
        print()
