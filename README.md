# Agent Regression Pilot

Decides whether changing an agent's model or instructions breaks a workflow — **before** deploy.

It runs a fixed case pack against a baseline and a candidate configuration, classifies every
result, and returns one of four verdicts with a matching exit code.

## Verdicts

| verdict | exit | meaning |
|---|---|---|
| `NO_REGRESSION_DETECTED` | 0 | no case deteriorated and nothing is unresolved. **Not a release approval.** |
| `REGRESSION` | 1 | a case that passed on the baseline fails on the candidate. |
| `REVIEW_REQUIRED` | 2 | subjective or inconsistent results are still unresolved. |
| `INFRA_FAILURE` | 3 | the run cannot be trusted: bad contract, failed process, missing evidence. |

## Invariants

These are enforced in code, not by convention:

- A process that exits non-zero **never** earns PASS, even when its stdout holds valid JSON.
- A pack that breaks the contract (missing cases, duplicate ids, `runs < 1`, fewer than two
  configs, unsupported expectation type, missing `cmd`) returns `INFRA_FAILURE` before any case runs.
- If the number of recorded executions differs from `cases × configs × runs`, the run is `INFRA_FAILURE`.
- A command that cannot start makes the run `INFRA_FAILURE`.
- Three repetitions must agree; a split is `UNCERTAIN`, never PASS.
- A subjective case stays `UNCERTAIN` until a named reviewer records a decision **with evidence**.

## Run it

```bash
python -m unittest discover -s tests -v          # tests for the gate itself

python runner.py --pack pack.json           --out out         --reviews reviews.example.json --json
python runner.py --pack pack.control.json   --out out_control --reviews reviews.example.json --json
python runner.py --pack pack.control.json   --out out_review  --json      # no reviews -> REVIEW_REQUIRED
```

Expected exit codes: `1`, `0`, `2`.

## Pack format

```json
{
  "workflow": "name",
  "runs": 3,
  "timeout_s": 60,
  "configs": [
    {"id": "baseline",  "identifier": "model+instruction hash", "cmd": ["python", "agents/baseline.py"]},
    {"id": "candidate", "identifier": "model+instruction hash", "cmd": ["python", "agents/candidate.py"]}
  ],
  "cases": [
    {"id": "C01", "input": {"subject": "..."}, "expect": {"type": "exact", "json": {"queue": "billing"}}}
  ]
}
```

| expect.type | verdict source |
|---|---|
| `exact` | whole JSON must equal `expect.json` |
| `fields` | only the named fields must equal |
| `human` | always `UNCERTAIN` until a reviewer records a decision |

## Review file

```json
{"decisions": {"C19": {"decision": "PASS", "reviewer": "name", "evidence": "what was checked and where", "at": "2026-11-16T22:40:00Z"}}}
```

A decision other than `PASS`/`FAIL`, or a missing `reviewer`/`evidence`/`at`, is a contract violation.

## Artifacts

`out/results.jsonl` — one row per execution: config, case, run, verdict, exit, why, ms, output hash
`out/report.json` — verdict, exit code, reasons, contract errors, per-case table, counts
`out/report.md` — the same, readable

## CI

`.github/workflows/agent-regression.yml` runs four jobs: the gate self-tests, the defect run
(must exit 1), the control run (must exit 0), and an unreviewed run (must exit 2). Each uploads its
evidence as an artifact.

## Status

Every case and both agents in this repository are **illustrative data**, not a customer workflow.
A green run here proves the gate works; it says nothing about any real workflow.
