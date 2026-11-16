"""runner.py — regression pilot for an AI agent configuration change.

Executes a fixed case pack against N configurations, R times each, classifies every
result, and exits non-zero when a regression is detected (so CI can gate a deploy).

    python runner.py --pack pack.json --out out [--fail-on-regression]

Case expectation kinds
    {"type": "exact", "json": {...}}    deterministic: output must equal this JSON
    {"type": "fields", "json": {...}}   deterministic: only the named fields must match
    {"type": "human", "rubric": "..."}  subjective: always UNCERTAIN, queued for a person
"""
import argparse
import json
import os
import subprocess
import sys
import time

PASS, FAIL, UNCERTAIN = "PASS", "FAIL", "UNCERTAIN"


def classify(expect, output):
    kind = expect.get("type", "exact")
    if kind == "human":
        return UNCERTAIN, "subjective: routed to human review"
    if output is None:
        return FAIL, "agent produced no parseable JSON"
    want = expect["json"]
    if kind == "exact":
        ok = output == want
        return (PASS if ok else FAIL), ("exact match" if ok else f"expected {json.dumps(want, sort_keys=True)}")
    if kind == "fields":
        bad = {k: (output.get(k), v) for k, v in want.items() if output.get(k) != v}
        return (PASS if not bad else FAIL), ("fields match" if not bad else f"mismatch {bad}")
    raise ValueError(f"unknown expectation type {kind}")


def run_one(cfg, case, timeout):
    payload = json.dumps({"case_id": case["id"], "input": case["input"]})
    t0 = time.time()
    try:
        p = subprocess.run(cfg["cmd"], input=payload, capture_output=True, text=True,
                           timeout=timeout, encoding="utf-8", errors="replace")
        out_txt = (p.stdout or "").strip()
        try:
            out = json.loads(out_txt) if out_txt else None
        except json.JSONDecodeError:
            out = None
        return {"exit": p.returncode, "output": out,
                "stderr_tail": (p.stderr or "")[-300:], "ms": round((time.time() - t0) * 1000)}
    except subprocess.TimeoutExpired:
        return {"exit": "timeout", "output": None, "stderr_tail": "", "ms": round((time.time() - t0) * 1000)}


def summarise(verdicts):
    n = len(verdicts)
    if all(v == PASS for v in verdicts):
        return PASS
    if all(v == FAIL for v in verdicts):
        return FAIL
    if all(v == UNCERTAIN for v in verdicts):
        return UNCERTAIN
    return UNCERTAIN  # mixed across runs: not trustworthy, escalate


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--pack", required=True)
    ap.add_argument("--out", default="out")
    ap.add_argument("--fail-on-regression", action="store_true")
    a = ap.parse_args()

    pack = json.load(open(a.pack, encoding="utf-8"))
    runs = pack.get("runs", 3)
    timeout = pack.get("timeout_s", 60)
    os.makedirs(a.out, exist_ok=True)

    records = []
    t_start = time.time()
    for cfg in pack["configs"]:
        for case in pack["cases"]:
            per_run = []
            for r in range(1, runs + 1):
                res = run_one(cfg, case, timeout)
                v, why = classify(case["expect"], res["output"])
                per_run.append({"run": r, "verdict": v, "why": why, **res})
                records.append({"config": cfg["id"], "case": case["id"], "run": r,
                                "verdict": v, "why": why, "ms": res["ms"],
                                "exit": res["exit"], "output": res["output"]})
            cfg.setdefault("_case", {})[case["id"]] = summarise([x["verdict"] for x in per_run])

    with open(os.path.join(a.out, "results.jsonl"), "w", encoding="utf-8") as f:
        for r in records:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")

    base = pack["configs"][0]["_case"]
    cand = pack["configs"][1]["_case"]
    regressions = [c for c in base if base[c] == PASS and cand[c] != PASS]
    fixes = [c for c in base if base[c] != PASS and cand[c] == PASS]
    broken_both = [c for c in base if base[c] == FAIL and cand[c] == FAIL]
    uncertain = [c for c in base if cand[c] == UNCERTAIN or base[c] == UNCERTAIN]

    elapsed = round(time.time() - t_start, 2)
    report = {
        "workflow": pack["workflow"],
        "configs": [{"id": c["id"], "name": c.get("name", c["id"]),
                     "identifier": c.get("identifier", "not supplied")} for c in pack["configs"]],
        "cases": len(pack["cases"]), "runs_per_case": runs,
        "executions": len(records), "elapsed_s": elapsed,
        "verdicts": {"baseline": base, "candidate": cand},
        "regressions": regressions, "fixes": fixes,
        "failing_in_both": broken_both, "needs_human": uncertain,
        "gate": "REGRESSION" if regressions else "CLEAR",
    }
    json.dump(report, open(os.path.join(a.out, "report.json"), "w", encoding="utf-8"),
              indent=2, ensure_ascii=False)

    lines = [f"# Regression report — {pack['workflow']}", "",
             f"- cases: {len(pack['cases'])} · runs/case: {runs} · executions: {len(records)} · wall: {elapsed}s",
             f"- gate: **{report['gate']}**", "",
             "| case | baseline | candidate |", "|---|---|---|"]
    for c in base:
        lines.append(f"| {c} | {base[c]} | {cand[c]} |")
    lines += ["", f"Regressions ({len(regressions)}): {', '.join(regressions) or 'none'}",
              f"Fixes ({len(fixes)}): {', '.join(fixes) or 'none'}",
              f"Failing in both ({len(broken_both)}): {', '.join(broken_both) or 'none'}",
              f"Needs human review ({len(uncertain)}): {', '.join(uncertain) or 'none'}"]
    open(os.path.join(a.out, "report.md"), "w", encoding="utf-8").write("\n".join(lines) + "\n")

    print(json.dumps({"gate": report["gate"], "executions": len(records),
                      "regressions": regressions, "fixes": fixes,
                      "needs_human": uncertain, "elapsed_s": elapsed}, ensure_ascii=False))
    if a.fail_on_regression and regressions:
        sys.exit(1)


if __name__ == "__main__":
    main()
