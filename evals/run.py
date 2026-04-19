"""
Eval runner for iMessage reply suggestions.

Usage:
  python -m evals.run                          # run all cases
  python -m evals.run --case empty-thread-new  # run a single case
  python -m evals.run --no-judge               # skip LLM-as-judge (faster)

Requires:
  ANTHROPIC_API_KEY to be set in the environment.
  The messaging agent at localhost:8767 to be running, OR use --offline to call
  llm.suggest_replies directly (in-process, no HTTP).

Results written to evals/results/<timestamp>.json.
"""

import argparse
import json
import os
import sys
import time
from pathlib import Path

# Allow running from repo root: python -m evals.run
sys.path.insert(0, str(Path(__file__).parent.parent))

from evals.judge import score_all_replies
from server.llm import suggest_replies
from server.messages_db import Message

CASES_PATH = Path(__file__).parent / "cases.json"
RESULTS_DIR = Path(__file__).parent / "results"


# ── helpers ──────────────────────────────────────────────────────────────────

def load_cases(filter_id: str = None) -> list[dict]:
    with open(CASES_PATH) as f:
        cases = json.load(f)
    if filter_id:
        cases = [c for c in cases if c["id"] == filter_id]
    return cases


def history_to_messages(history: list[dict]) -> list[Message]:
    """Convert eval case history (with ts_offset_min) to Message objects."""
    now = time.time()
    return [
        Message(
            id=i,
            text=h["text"],
            from_me=h.get("from_me", False),
            ts=now + h.get("ts_offset_min", 0) * 60,
        )
        for i, h in enumerate(history)
    ]


def parse_rubric(rubric_str: str) -> tuple[str, int]:
    """'>=4' → ('>=', 4)"""
    for op in (">=", "<=", "=", ">", "<"):
        if rubric_str.startswith(op):
            return op, int(rubric_str[len(op):])
    return ">=", int(rubric_str)


def rubric_pass(score: int, rubric_str: str) -> bool:
    op, val = parse_rubric(rubric_str)
    return {">=": score >= val, "<=": score <= val, "=": score == val,
            ">": score > val, "<": score < val}[op]


def check_rules(replies: list[str], case: dict) -> tuple[bool, str]:
    text = " ".join(replies).lower()
    for term in case.get("must_avoid_terms", []):
        if term.lower() in text:
            return False, f"must_avoid hit: {term!r}"
    must_include = case.get("must_include_one_of", [])
    if must_include:
        if not any(t.lower() in text for t in must_include):
            return False, f"none of must_include_one_of matched: {must_include}"
    for r in replies:
        if len(r) > 120:
            return False, f"reply too long ({len(r)} chars): {r[:40]}…"
    return True, ""


def _col(width, text, colour=""):
    s = str(text)[:width].ljust(width)
    RESET = "\033[0m"
    return f"{colour}{s}{RESET}" if colour else s


GREEN = "\033[32m"
RED   = "\033[31m"
DIM   = "\033[2m"
BOLD  = "\033[1m"


# ── main ─────────────────────────────────────────────────────────────────────

def run_case(case: dict, run_judge: bool) -> dict:
    case_id = case["id"]
    contact_name = case["contact_name"]
    history_raw = case.get("history", [])
    thread = history_to_messages(history_raw)
    expected_intent = case.get("expected_intent", "continuation")

    t0 = time.perf_counter()
    try:
        result = suggest_replies(contact_name=contact_name, thread=thread)
    except Exception as e:
        return {
            "id": case_id, "status": "ERROR", "error": str(e),
            "latency_ms": round((time.perf_counter() - t0) * 1000),
        }
    latency_ms = round((time.perf_counter() - t0) * 1000)

    intent = result.get("intent", "")
    replies = result.get("replies", [])

    # Intent check
    intent_ok = intent == expected_intent

    # Rule checks
    rules_ok, rules_fail_reason = check_rules(replies, case)

    # Judge
    judge_scores = []
    judge_ok = True
    if run_judge and replies:
        judge_scores = score_all_replies(contact_name, history_raw, replies)
        rubric = case.get("judge_rubric", {})
        for metric, rubric_str in rubric.items():
            scores_for_metric = [s.get(metric, 0) for s in judge_scores]
            avg = sum(scores_for_metric) / len(scores_for_metric) if scores_for_metric else 0
            if not rubric_pass(round(avg), rubric_str):
                judge_ok = False

    status = "PASS" if (intent_ok and rules_ok and judge_ok) else "FAIL"
    fail_reasons = []
    if not intent_ok:
        fail_reasons.append(f"intent={intent!r} expected={expected_intent!r}")
    if not rules_ok:
        fail_reasons.append(rules_fail_reason)
    if not judge_ok:
        fail_reasons.append("judge rubric not met")

    return {
        "id": case_id,
        "status": status,
        "intent": intent,
        "intent_ok": intent_ok,
        "rules_ok": rules_ok,
        "judge_ok": judge_ok,
        "replies": replies,
        "judge_scores": judge_scores,
        "latency_ms": latency_ms,
        "fail_reasons": fail_reasons,
    }


def main():
    parser = argparse.ArgumentParser(description="Run iMessage reply eval suite")
    parser.add_argument("--case", help="Run only this case ID")
    parser.add_argument("--no-judge", action="store_true", help="Skip LLM-as-judge scoring")
    args = parser.parse_args()

    cases = load_cases(args.case)
    if not cases:
        print(f"No cases found{f' for id={args.case!r}' if args.case else ''}.")
        sys.exit(1)

    run_judge = not args.no_judge
    RESULTS_DIR.mkdir(parents=True, exist_ok=True)

    print(f"\n{BOLD}iMessage Reply Eval — {len(cases)} case(s){' (no judge)' if not run_judge else ''}\033[0m\n")

    col_w = [36, 8, 6, 6, 8]
    header = (
        _col(col_w[0], "CASE ID", BOLD) +
        _col(col_w[1], "INTENT", BOLD) +
        _col(col_w[2], "RULES", BOLD) +
        _col(col_w[3], "JUDGE", BOLD) +
        _col(col_w[4], "LAT(ms)", BOLD) +
        f"{BOLD}STATUS\033[0m"
    )
    print(header)
    print("─" * (sum(col_w) + 6))

    all_results = []
    latencies = []

    for case in cases:
        print(f"  Running {case['id']}… ", end="", flush=True)
        r = run_case(case, run_judge)
        all_results.append(r)

        if r["status"] == "ERROR":
            print(f"\r{_col(col_w[0], r['id'])} ERROR: {r.get('error','')}")
            continue

        latencies.append(r["latency_ms"])
        intent_sym = "✓" if r["intent_ok"] else "✗"
        rules_sym  = "✓" if r["rules_ok"]  else "✗"
        judge_sym  = "✓" if r["judge_ok"]  else ("✗" if run_judge else "–")
        status_col = GREEN if r["status"] == "PASS" else RED
        fail_note  = f"  {DIM}({'; '.join(r['fail_reasons'])})\033[0m" if r["fail_reasons"] else ""

        print(
            f"\r{_col(col_w[0], r['id'])}"
            f"{_col(col_w[1], intent_sym, GREEN if r['intent_ok'] else RED)}"
            f"{_col(col_w[2], rules_sym,  GREEN if r['rules_ok']  else RED)}"
            f"{_col(col_w[3], judge_sym,  GREEN if r['judge_ok']  else RED)}"
            f"{_col(col_w[4], r['latency_ms'])}"
            f"{_col(6, r['status'], status_col)}"
            f"{fail_note}"
        )
        if not r["rules_ok"] or not r["judge_ok"]:
            for rep in r.get("replies", []):
                print(f"    {DIM}→ {rep}\033[0m")

    # Summary
    passed = sum(1 for r in all_results if r.get("status") == "PASS")
    total  = len(all_results)
    p50 = sorted(latencies)[len(latencies)//2] if latencies else 0
    p95 = sorted(latencies)[int(len(latencies)*0.95)] if latencies else 0

    print("\n" + "─" * (sum(col_w) + 6))
    pct = passed / total * 100 if total else 0
    status_col = GREEN if pct >= 80 else RED
    print(
        f"{BOLD}Overall: {status_col}{passed}/{total} ({pct:.0f}%)\033[0m  "
        f"p50={p50}ms  p95={p95}ms\n"
    )

    # Write results
    ts = time.strftime("%Y%m%d_%H%M%S")
    out_path = RESULTS_DIR / f"{ts}.json"
    with open(out_path, "w") as f:
        json.dump({
            "run_at": ts,
            "total": total,
            "passed": passed,
            "p50_ms": p50,
            "p95_ms": p95,
            "cases": all_results,
        }, f, indent=2)
    print(f"{DIM}Results saved to {out_path}\033[0m\n")

    sys.exit(0 if passed == total else 1)


if __name__ == "__main__":
    main()
