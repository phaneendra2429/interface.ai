import json
import sys
from pathlib import Path

repo_root = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(repo_root / "harness"))
sys.path.insert(0, str(repo_root))

from contract import Page, Snapshot
from run_eval import load_snapshots
from scoring import score_item
import solution

# Ingest all snapshots using official loader
snapshots = load_snapshots(repo_root / "snapshots")
ks = solution.build()
for snap in snapshots:
    ks.ingest(snap)

# Score questions
with open("eval/public_eval.jsonl", encoding="utf-8") as f:
    eval_items = [json.loads(line) for line in f]

for item in eval_items:
    ans = ks.answer(item["question"], as_of=item.get("as_of"))
    score = score_item(item, ans)
    item_score = score.get("score", 0.0)
    if item_score < 0.99:
        print(f"=== Question {item['id']} ({item.get('category')}) Score: {item_score} ===")
        print("  Q:", item["question"])
        print("  as_of:", item.get("as_of"))
        print("  Expected:", item["expected_answer"])
        print("  Must include:", item["must_include"])
        print("  Must NOT include:", item["must_not_include"])
        print("  Got text:", ans.text)
        print("  Confident:", ans.confident)
        print("  Sources:", [s.url for s in ans.sources])
        print("  Score detail:", score)
