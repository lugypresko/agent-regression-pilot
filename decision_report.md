# Decision report — regression pilot (MECHANICS ONLY)

Generated 2026-11-16T22:01:11Z

> **This is not a customer result.** Every case and both agents are illustrative data.
> It proves the test pack's mechanics, not any customer workflow.

## What was run

| run | configs | cases | executions | gate | exit | wall |
|---|---|---|---|---|---|---|
| defect-injected | baseline vs candidate (2 defects) | 20 | 120 | **REGRESSION** | 1 | 11.65s |
| control (no defects) | baseline vs identical copy | 20 | 120 | **CLEAR** | 0 | 12.59s |

## Finding — defect run

Regressions detected: **C02, C05, C18**

| case | baseline | candidate | cause |
|---|---|---|---|
| C02 | PASS | FAIL | defect D1: 'refund' no longer routes to billing |
| C05 | PASS | FAIL | defect D2: 'down' lowers priority instead of raising it |
| C18 | PASS | FAIL | same defect D2, caught on a second case |

Requires human review: **C19, C20** (subjective cases, never PASS/FAIL)

## Finding — control run

No regressions. The pack does not fire on an unchanged agent, so the three findings above
are attributable to the injected defects and not to run-to-run noise.

## What the evidence supports

- The pack distinguishes a passing case from a regressed one at 3 runs per case.
- Mixed results across runs surface as UNCERTAIN rather than a guess.
- Subjective cases are never auto-accepted; they are queued for a person.
- The gate fails CI (exit 1) when a regression exists and passes (exit 0) when none does.

## What it does not support

- Any claim about a customer workflow, model or instruction change.
- Release decisions of any kind.

## Reproduce

```
python runner.py --pack pack.json --out out --fail-on-regression
python runner.py --pack pack.control.json --out out_control --fail-on-regression
```
