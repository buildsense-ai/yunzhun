"""Extractor evaluation harness.

Scores app.mail.storagelinks.extract_storage_refs against a labeled golden set
(eval/golden.jsonl). Use it to judge whether an extractor change is a net
improvement before shipping it.

Usage:
    pdm run eval                     # score + compare against eval/baseline.json
    pdm run eval --update-baseline   # write current metrics as new baseline
    pdm run eval --fail-below 0.95   # gate: exit 1 if F1 drops below threshold

Exit codes: 0 = pass, 1 = regression/threshold failure, 2 = usage error.
"""
from __future__ import annotations

import argparse
import json
import sys
from collections import defaultdict
from pathlib import Path

from app.mail.storagelinks import extract_storage_refs

EVAL_DIR = Path(__file__).resolve().parent
GOLDEN_PATH = EVAL_DIR / "golden.jsonl"
BASELINE_PATH = EVAL_DIR / "baseline.json"


def load_cases(path: Path = GOLDEN_PATH) -> list[dict]:
    cases = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line and not line.startswith("//"):
            cases.append(json.loads(line))
    return cases


def _expected_tuples(case: dict) -> set[tuple]:
    return {
        (e["provider"], e["bucket"], e["key"], bool(e.get("presigned", False)))
        for e in case["expect"]
    }


def score(cases: list[dict]) -> dict:
    tp = fp = fn = 0
    per_provider: dict[str, dict[str, int]] = defaultdict(lambda: {"tp": 0, "fp": 0, "fn": 0})
    failures: list[dict] = []

    for case in cases:
        got = extract_storage_refs(case.get("text"), case.get("html"))
        got_tuples = {(r.provider, r.bucket, r.key, r.presigned) for r in got}
        exp_tuples = _expected_tuples(case)

        for t in got_tuples & exp_tuples:
            tp += 1
            per_provider[t[0]]["tp"] += 1
        for t in got_tuples - exp_tuples:
            fp += 1
            per_provider[t[0]]["fp"] += 1
            failures.append({"case": case["id"], "type": "FP", "got": t})
        for t in exp_tuples - got_tuples:
            fn += 1
            per_provider[t[0]]["fn"] += 1
            failures.append({"case": case["id"], "type": "FN", "missed": t})

    def prf(p: dict[str, int]) -> tuple[float, float, float]:
        precision = p["tp"] / (p["tp"] + p["fp"]) if p["tp"] + p["fp"] else 1.0
        recall = p["tp"] / (p["tp"] + p["fn"]) if p["tp"] + p["fn"] else 1.0
        f1 = 2 * precision * recall / (precision + recall) if precision + recall else 0.0
        return round(precision, 4), round(recall, 4), round(f1, 4)

    metrics: dict = {"tp": tp, "fp": fp, "fn": fn}
    metrics["precision"], metrics["recall"], metrics["f1"] = prf(
        {"tp": tp, "fp": fp, "fn": fn}
    )
    metrics["per_provider"] = {
        prov: dict(zip(("precision", "recall", "f1"), prf(p))) | {"tp": p["tp"], "fp": p["fp"], "fn": p["fn"]}
        for prov, p in sorted(per_provider.items())
    }
    metrics["failures"] = failures
    return metrics


def render(metrics: dict) -> str:
    lines = [
        f"overall: P={metrics['precision']:.4f} R={metrics['recall']:.4f} F1={metrics['f1']:.4f}"
        f"  (tp={metrics['tp']} fp={metrics['fp']} fn={metrics['fn']})",
    ]
    for prov, m in metrics["per_provider"].items():
        lines.append(
            f"  {prov:14} P={m['precision']:.3f} R={m['recall']:.3f} F1={m['f1']:.3f}"
            f"  (tp={m['tp']} fp={m['fp']} fn={m['fn']})"
        )
    for f in metrics["failures"]:
        target = f.get("got") or f.get("missed")
        lines.append(f"  {f['type']} in {f['case']}: {target}")
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--update-baseline", action="store_true", help="write current metrics as baseline")
    parser.add_argument("--fail-below", type=float, default=None, metavar="F1", help="gate on minimum overall F1")
    args = parser.parse_args()

    cases = load_cases()
    metrics = score(cases)
    print(render(metrics))

    exit_code = 0
    if BASELINE_PATH.exists():
        baseline = json.loads(BASELINE_PATH.read_text())
        for key in ("precision", "recall", "f1"):
            if metrics[key] < baseline[key]:
                print(f"REGRESSION: {key} {metrics[key]} < baseline {baseline[key]}")
                exit_code = 1
    if args.fail_below is not None and metrics["f1"] < args.fail_below:
        print(f"THRESHOLD: F1 {metrics['f1']} < {args.fail_below}")
        exit_code = 1
    if args.update_baseline:
        BASELINE_PATH.write_text(
            json.dumps({k: v for k, v in metrics.items() if k != "failures"}, indent=2, ensure_ascii=False) + "\n"
        )
        print(f"baseline updated -> {BASELINE_PATH.relative_to(EVAL_DIR.parent)}")

    if not metrics["failures"]:
        print("all golden cases pass")
    return exit_code


if __name__ == "__main__":
    sys.exit(main())
