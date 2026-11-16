# Agent Regression Pilot

Decides whether changing an agent's model or instructions breaks a business workflow — before deploy.

## Run
    python runner.py --pack pack.json --out out --fail-on-regression

Exit code 1 when a case that passed on the baseline fails on the candidate.

## Pack format
    {
      "workflow": "name",
      "runs": 3,
      "timeout_s": 60,
      "configs": [
        {"id":"baseline","name":"...","identifier":"model+instruction hash","cmd":["..."]},
        {"id":"candidate","name":"...","identifier":"...","cmd":["..."]}
      ],
      "cases": [{"id":"C01","input":{...},"expect":{"type":"exact","json":{...}}}]
    }

## Expectation kinds
| type | meaning | verdict source |
|---|---|---|
| `exact` | whole JSON must equal | deterministic |
| `fields` | named fields must equal | deterministic |
| `human` | subjective | always UNCERTAIN, queued for review |

## Result aggregation
3/3 PASS → PASS · 0/3 PASS → FAIL · anything else → UNCERTAIN (mixed runs are not trustworthy).

## Artifacts
`out/results.jsonl` (every execution) · `out/report.json` · `out/report.md`

## Status
`pack.example.json` and `agents/*.py` are **illustrative data**, not a customer workflow.
