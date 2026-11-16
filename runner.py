"""runner.py — regression gate for an AI agent configuration change.

Four explicit verdicts, each with a distinct exit code:

    NO_REGRESSION_DETECTED  0   no case deteriorated and nothing is unresolved.
                                This is NOT release approval.
    REGRESSION              1   a case that passed on the baseline fails on the candidate.
    REVIEW_REQUIRED         2   subjective or inconsistent results are still unresolved.
    INFRA_FAILURE           3   the run could not be trusted: bad contract, failed process,
                                missing repetitions, missing evidence.

Invariants enforced here, not documented elsewhere:
  * a process that exits non-zero never earns PASS, whatever its stdout says;
  * a pack that breaks the contract never produces a verdict other than INFRA_FAILURE;
  * an execution whose evidence is missing makes the whole run INFRA_FAILURE;
  * a subjective case stays unresolved until a named reviewer records a decision.

usage:
    python runner.py --pack pack.json --out out [--reviews reviews.json] [--json]
"""
import argparse
import hashlib
import json
import os
import subprocess
import sys
import time

PASS, FAIL, UNCERTAIN = "PASS", "FAIL", "UNCERTAIN"
VERDICT_EXIT = {
    "NO_REGRESSION_DETECTED": 0,
    "REGRESSION": 1,
    "REVIEW_REQUIRED": 2,
    "INFRA_FAILURE": 3,
}
SUPPORTED_EXPECT = {"exact", "fields", "human"}


class ContractError(Exception):
    """The pack cannot be executed as written."""


# --------------------------------------------------------------------- contract
def validate_pack(pack):
    """Return a list of contract violations. Empty means the pack may run."""
    errs = []
    if not isinstance(pack, dict):
        return ["pack is not a JSON object"]
    if not isinstance(pack.get("workflow"), str) or not pack["workflow"].strip():
        errs.append("workflow: missing or empty")
    runs = pack.get("runs")
    if not isinstance(runs, int) or runs < 1:
        errs.append(f"runs: must be an integer >= 1, got {runs!r}")
    cfgs = pack.get("configs")
    if not isinstance(cfgs, list) or len(cfgs) < 2:
        errs.append(f"configs: need at least 2, got {len(cfgs) if isinstance(cfgs, list) else 'none'}")
    else:
        seen = set()
        for i, c in enumerate(cfgs):
            cid = c.get("id") if isinstance(c, dict) else None
            if not cid:
                errs.append(f"configs[{i}]: missing id")
                continue
            if cid in seen:
                errs.append(f"configs[{i}]: duplicate id {cid!r}")
            seen.add(cid)
            cmd = c.get("cmd")
            if not isinstance(cmd, list) or not cmd or not all(isinstance(x, str) for x in cmd):
                errs.append(f"configs[{i}] ({cid}): cmd must be a non-empty list of strings")

    cases = pack.get("cases")
    if not isinstance(cases, list) or not cases:
        errs.append("cases: must be a non-empty list")
        return errs
    ids = set()
    for i, c in enumerate(cases):
        cid = c.get("id") if isinstance(c, dict) else None
        if not cid:
            errs.append(f"cases[{i}]: missing id")
            continue
        if cid in ids:
            errs.append(f"cases[{i}]: duplicate id {cid!r}")
        ids.add(cid)
        if "input" not in c:
            errs.append(f"case {cid}: missing input")
        exp = c.get("expect")
        if not isinstance(exp, dict):
            errs.append(f"case {cid}: missing expect")
            continue
        kind = exp.get("type")
        if kind not in SUPPORTED_EXPECT:
            errs.append(f"case {cid}: unsupported expectation type {kind!r}")
        elif kind == "human":
            if not isinstance(exp.get("rubric"), str) or not exp["rubric"].strip():
                errs.append(f"case {cid}: human case needs a rubric")
        elif not isinstance(exp.get("json"), dict):
            errs.append(f"case {cid}: {kind} expectation needs a json object")
    return errs


# ------------------------------------------------------------------ execution
def _sha(text):
    return hashlib.sha256(text.encode("utf-8", "replace")).hexdigest()[:16] if text else None


def run_one(cfg, case, timeout):
    payload = json.dumps({"case_id": case["id"], "input": case["input"]})
    t0 = time.time()
    rec = {"config": cfg["id"], "case": case["id"], "command": " ".join(cfg["cmd"])}
    try:
        p = subprocess.run(cfg["cmd"], input=payload, capture_output=True, text=True,
                           timeout=timeout, encoding="utf-8", errors="replace")
        out_txt = (p.stdout or "").strip()
        try:
            parsed = json.loads(out_txt) if out_txt else None
        except json.JSONDecodeError:
            parsed = None
        rec.update({"exit": p.returncode, "output": parsed, "raw": out_txt,
                    "raw_sha": _sha(out_txt), "stderr_sha": _sha(p.stderr or ""),
                    "ms": round((time.time() - t0) * 1000), "timed_out": False})
    except subprocess.TimeoutExpired:
        rec.update({"exit": "timeout", "output": None, "raw": "", "raw_sha": None,
                    "stderr_sha": None, "ms": round((time.time() - t0) * 1000), "timed_out": True})
    except FileNotFoundError as e:
        rec.update({"exit": "not-found", "output": None, "raw": "", "raw_sha": None,
                    "stderr_sha": None, "ms": round((time.time() - t0) * 1000),
                    "timed_out": False, "spawn_error": str(e)})
    return rec


def classify(expect, rec):
    """EXIT-CODE INTEGRITY: a non-zero exit is FAIL, even with valid JSON on stdout."""
    kind = expect.get("type")
    if kind == "human":
        return UNCERTAIN, "subjective: awaiting an authorised reviewer"
    if rec.get("timed_out"):
        return FAIL, "process timed out"
    if rec.get("spawn_error"):
        return FAIL, f"process could not start: {rec['spawn_error']}"
    if rec.get("exit") != 0:
        return FAIL, f"process exited {rec.get('exit')}"
    out = rec.get("output")
    if out is None:
        return FAIL, "no parseable JSON on stdout"
    want = expect["json"]
    if kind == "exact":
        return (PASS, "matches expected") if out == want else (FAIL, f"expected {json.dumps(want, sort_keys=True)}")
    if kind == "fields":
        bad = {k: (out.get(k), v) for k, v in want.items() if out.get(k) != v}
        return (PASS, "fields match") if not bad else (FAIL, f"field mismatch {bad}")
    return FAIL, f"unsupported expectation {kind!r}"


def summarise(verdicts):
    """Three repetitions must agree. A split is not a pass."""
    if all(v == PASS for v in verdicts):
        return PASS, f"{len(verdicts)}/{len(verdicts)} runs passed"
    if all(v == FAIL for v in verdicts):
        return FAIL, f"0/{len(verdicts)} runs passed"
    if all(v == UNCERTAIN for v in verdicts):
        return UNCERTAIN, "subjective in every run"
    return UNCERTAIN, f"inconsistent across runs: {verdicts}"


# ---------------------------------------------------------------------- reviews
def load_reviews(path):
    if not path:
        return {}, []
    try:
        data = json.load(open(path, encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as e:
        return {}, [f"reviews file unreadable: {e}"]
    errs, out = [], {}
    for cid, r in (data.get("decisions") or {}).items():
        if r.get("decision") not in (PASS, FAIL):
            errs.append(f"review {cid}: decision must be PASS or FAIL")
            continue
        for field in ("reviewer", "evidence", "at"):
            if not r.get(field):
                errs.append(f"review {cid}: missing {field}")
        out[cid] = {"decision": r["decision"], "reviewer": r.get("reviewer"),
                    "evidence": r.get("evidence"), "at": r.get("at")}
    return out, errs


# ------------------------------------------------------------------------- main
def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--pack", required=True)
    ap.add_argument("--out", default="out")
    ap.add_argument("--reviews")
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args()

    os.makedirs(a.out, exist_ok=True)
    started = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    t0 = time.time()

    report = {"started_at": started, "pack": a.pack, "verdict": None,
              "exit_code": None, "reasons": [], "contract_errors": [],
              "executions": 0, "expected_executions": 0, "wall_s": None,
              "configs": [], "cases": 0, "runs_per_case": None,
              "regressions": [], "fixes": [], "failing_in_both": [],
              "unresolved": [], "reviews_applied": {}, "evidence_file": None}

    # --- contract
    try:
        pack = json.load(open(a.pack, encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as e:
        pack = None
        report["contract_errors"] = [f"pack unreadable: {e}"]
    if pack is not None:
        report["contract_errors"] = validate_pack(pack)

    reviews, review_errs = load_reviews(a.reviews)
    report["contract_errors"] += review_errs

    if report["contract_errors"]:
        report["verdict"] = "INFRA_FAILURE"
        report["reasons"] = ["pack or reviews violate the contract"]
        report["exit_code"] = VERDICT_EXIT["INFRA_FAILURE"]
        return finish(report, a.out, t0, a.json)

    report["configs"] = [{"id": c["id"], "name": c.get("name", c["id"]),
                          "identifier": c.get("identifier", "not supplied")} for c in pack["configs"]]
    report["cases"] = len(pack["cases"])
    report["runs_per_case"] = pack["runs"]
    report["expected_executions"] = len(pack["cases"]) * len(pack["configs"]) * pack["runs"]

    # --- execute
    records = []
    per = {}
    for cfg in pack["configs"]:
        for case in pack["cases"]:
            runs = []
            for r in range(1, pack["runs"] + 1):
                rec = run_one(cfg, case, pack.get("timeout_s", 60))
                v, why = classify(case["expect"], rec)
                rec.update({"run": r, "verdict": v, "why": why})
                records.append(rec)
                runs.append(v)
            agg, why = summarise(runs)
            per.setdefault(cfg["id"], {})[case["id"]] = {"verdict": agg, "why": why}

    evidence_path = os.path.join(a.out, "results.jsonl")
    with open(evidence_path, "w", encoding="utf-8") as f:
        for r in records:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")
    report["evidence_file"] = evidence_path
    report["executions"] = len(records)

    # --- evidence integrity
    infra = []
    if len(records) != report["expected_executions"]:
        infra.append(f"executed {len(records)} of {report['expected_executions']} planned executions")
    if not os.path.exists(evidence_path) or os.path.getsize(evidence_path) == 0:
        infra.append("evidence file missing or empty")
    spawn = [r for r in records if r.get("spawn_error")]
    if spawn:
        infra.append(f"{len(spawn)} execution(s) could not start")

    # --- apply human reviews
    for cid, r in per.get(pack["configs"][1]["id"], {}).items():
        if r["verdict"] == UNCERTAIN and cid in reviews:
            r["verdict"] = reviews[cid]["decision"]
            r["why"] = f"reviewed by {reviews[cid]['reviewer']}: {reviews[cid]['evidence']}"
    for cid, r in per.get(pack["configs"][0]["id"], {}).items():
        if r["verdict"] == UNCERTAIN and cid in reviews:
            r["verdict"] = reviews[cid]["decision"]
            r["why"] = f"reviewed by {reviews[cid]['reviewer']}: {reviews[cid]['evidence']}"
    report["reviews_applied"] = {k: v["decision"] for k, v in reviews.items()}

    base = per[pack["configs"][0]["id"]]
    cand = per[pack["configs"][1]["id"]]
    report["regressions"] = [c for c in base if base[c]["verdict"] == PASS and cand[c]["verdict"] == FAIL]
    report["fixes"] = [c for c in base if base[c]["verdict"] == FAIL and cand[c]["verdict"] == PASS]
    report["failing_in_both"] = [c for c in base if base[c]["verdict"] == FAIL and cand[c]["verdict"] == FAIL]
    report["unresolved"] = sorted({c for c in base if base[c]["verdict"] == UNCERTAIN}
                                  | {c for c in cand if cand[c]["verdict"] == UNCERTAIN})
    report["per_case"] = per

    # --- verdict, in priority order
    if infra:
        report["verdict"] = "INFRA_FAILURE"
        report["reasons"] = infra
    elif report["regressions"]:
        report["verdict"] = "REGRESSION"
        report["reasons"] = [f"{len(report['regressions'])} case(s) deteriorated: {', '.join(report['regressions'])}"]
    elif report["unresolved"]:
        report["verdict"] = "REVIEW_REQUIRED"
        report["reasons"] = [f"{len(report['unresolved'])} case(s) unresolved: {', '.join(report['unresolved'])}"]
    else:
        report["verdict"] = "NO_REGRESSION_DETECTED"
        report["reasons"] = ["no case deteriorated; this is not a release approval"]

    report["exit_code"] = VERDICT_EXIT[report["verdict"]]
    return finish(report, a.out, t0, a.json)


def finish(report, out_dir, t0, as_json):
    report["wall_s"] = round(time.time() - t0, 2)
    with open(os.path.join(out_dir, "report.json"), "w", encoding="utf-8") as f:
        json.dump(report, f, indent=2, ensure_ascii=False)
    with open(os.path.join(out_dir, "report.md"), "w", encoding="utf-8") as f:
        f.write(render(report))
    if as_json:
        print(json.dumps({k: report[k] for k in
                          ("verdict", "exit_code", "reasons", "executions",
                           "expected_executions", "regressions", "unresolved", "wall_s")},
                         ensure_ascii=False))
    else:
        print(f"{report['verdict']} (exit {report['exit_code']}) — {'; '.join(report['reasons'])}")
    sys.exit(report["exit_code"])


def render(r):
    L = [f"# Regression gate — {r['verdict']}", "",
         f"- exit code: **{r['exit_code']}**",
         f"- executions: {r['executions']} / {r['expected_executions']} planned",
         f"- wall: {r['wall_s']}s",
         f"- configs: " + ", ".join(f"{c['id']} ({c['identifier']})" for c in r["configs"]),
         f"- reasons: " + "; ".join(r["reasons"]), ""]
    if r["contract_errors"]:
        L += ["## Contract violations", ""] + [f"- {e}" for e in r["contract_errors"]] + [""]
    if r.get("per_case"):
        ids = list(r["per_case"][r["configs"][0]["id"]])
        L += ["| case | " + " | ".join(c["id"] for c in r["configs"]) + " |",
              "|---" * (len(r["configs"]) + 1) + "|"]
        for c in ids:
            L.append("| " + c + " | " + " | ".join(r["per_case"][x["id"]][c]["verdict"] for x in r["configs"]) + " |")
        L.append("")
    L += [f"Regressions: {', '.join(r['regressions']) or 'none'}",
          f"Unresolved: {', '.join(r['unresolved']) or 'none'}",
          f"Fixes: {', '.join(r['fixes']) or 'none'}",
          f"Failing in both: {', '.join(r['failing_in_both']) or 'none'}"]
    return "\n".join(L) + "\n"


if __name__ == "__main__":
    main()
