"""Tests for the regression gate itself.

Every test drives runner.py as a subprocess and asserts on its exit code and
machine-readable report, so the gate is tested the way CI uses it.
"""
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
FIX = os.path.join(ROOT, "tests", "fixtures")
RUNNER = os.path.join(ROOT, "runner.py")
PY = sys.executable
EXIT = {"NO_REGRESSION_DETECTED": 0, "REGRESSION": 1, "REVIEW_REQUIRED": 2, "INFRA_FAILURE": 3}


def agent(name):
    return [PY, os.path.join(FIX, name)]


def case(cid="C1", kind="exact", value=None, rubric=None):
    c = {"id": cid, "input": {"subject": "Invoice 1 is wrong"},
         "expect": {"type": kind, "json": value if value is not None else {"queue": "billing", "priority": "low"}}}
    if rubric:
        c["expect"]["rubric"] = rubric
    return c


class GateCase(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp(prefix="gate-")
        self.out = os.path.join(self.dir, "out")

    def tearDown(self):
        shutil.rmtree(self.dir, ignore_errors=True)

    def write(self, name, obj):
        p = os.path.join(self.dir, name)
        with open(p, "w", encoding="utf-8") as f:
            json.dump(obj, f)
        return p

    def run_gate(self, pack, reviews=None):
        args = [PY, RUNNER, "--pack", self.write("pack.json", pack),
                "--out", self.out, "--json"]
        if reviews is not None:
            args += ["--reviews", self.write("reviews.json", reviews)]
        p = subprocess.run(args, capture_output=True, text=True, encoding="utf-8", errors="replace")
        rep = {}
        rp = os.path.join(self.out, "report.json")
        if os.path.exists(rp):
            with open(rp, encoding="utf-8") as f:
                rep = json.load(f)
        return p.returncode, rep, p

    def pack(self, baseline="agent_ok.py", candidate="agent_ok.py", cases=None, runs=1):
        return {"workflow": "test", "runs": runs, "timeout_s": 5,
                "configs": [{"id": "baseline", "cmd": agent(baseline)},
                            {"id": "candidate", "cmd": agent(candidate)}],
                "cases": cases if cases is not None else [case()]}


class TestExitCodeIntegrity(GateCase):
    def test_nonzero_exit_with_valid_json_is_not_pass(self):
        code, rep, _ = self.run_gate(self.pack(candidate="agent_exit1_valid_json.py"))
        self.assertEqual(code, EXIT["REGRESSION"], rep.get("reasons"))
        self.assertEqual(rep["regressions"], ["C1"])

    def test_nonjson_stdout_is_not_pass(self):
        code, rep, _ = self.run_gate(self.pack(candidate="agent_garbage.py"))
        self.assertEqual(code, EXIT["REGRESSION"])

    def test_fake_completion_is_not_pass(self):
        code, rep, _ = self.run_gate(self.pack(candidate="agent_fake_done.py"))
        self.assertEqual(code, EXIT["REGRESSION"], "an agent that only says DONE must not pass")

    def test_timeout_is_not_pass(self):
        code, rep, _ = self.run_gate(self.pack(candidate="agent_timeout.py"))
        self.assertEqual(code, EXIT["REGRESSION"])
        with open(os.path.join(self.out, "results.jsonl"), encoding="utf-8") as f:
            rows = [json.loads(l) for l in f if l.strip()]
        self.assertTrue(any("timed out" in r["why"] for r in rows),
                        "a timed-out execution must record why it failed")

    def test_wrong_answer_is_not_pass(self):
        code, _, _ = self.run_gate(self.pack(candidate="agent_wrong.py"))
        self.assertEqual(code, EXIT["REGRESSION"])


class TestContractEnforcement(GateCase):
    def test_unsupported_expectation_type(self):
        bad = case()
        bad["expect"] = {"type": "vibes", "json": {}}
        code, rep, _ = self.run_gate(self.pack(cases=[bad]))
        self.assertEqual(code, EXIT["INFRA_FAILURE"])
        self.assertTrue(any("unsupported expectation" in e for e in rep["contract_errors"]))

    def test_duplicate_case_ids(self):
        code, rep, _ = self.run_gate(self.pack(cases=[case("C1"), case("C1")]))
        self.assertEqual(code, EXIT["INFRA_FAILURE"])
        self.assertTrue(any("duplicate" in e for e in rep["contract_errors"]))

    def test_missing_cases(self):
        p = self.pack()
        p["cases"] = []
        code, rep, _ = self.run_gate(p)
        self.assertEqual(code, EXIT["INFRA_FAILURE"])

    def test_zero_runs(self):
        code, rep, _ = self.run_gate(self.pack(runs=0))
        self.assertEqual(code, EXIT["INFRA_FAILURE"])

    def test_single_config(self):
        p = self.pack()
        p["configs"] = p["configs"][:1]
        code, _, _ = self.run_gate(p)
        self.assertEqual(code, EXIT["INFRA_FAILURE"])

    def test_missing_command(self):
        p = self.pack()
        p["configs"][1]["cmd"] = []
        code, _, _ = self.run_gate(p)
        self.assertEqual(code, EXIT["INFRA_FAILURE"])

    def test_unrunnable_command_is_infra_failure(self):
        p = self.pack()
        p["configs"][1]["cmd"] = [os.path.join(self.dir, "does-not-exist")]
        code, rep, _ = self.run_gate(p)
        self.assertEqual(code, EXIT["INFRA_FAILURE"], rep.get("reasons"))
        self.assertTrue(any("could not start" in r for r in rep["reasons"]))

    def test_bad_review_decision(self):
        code, rep, _ = self.run_gate(
            self.pack(cases=[case("C1", "human", rubric="tone")]),
            reviews={"decisions": {"C1": {"decision": "MAYBE", "reviewer": "x", "evidence": "y", "at": "z"}}})
        self.assertEqual(code, EXIT["INFRA_FAILURE"])


class TestVerdictSemantics(GateCase):
    def test_injected_defect_detected(self):
        code, rep, _ = self.run_gate(self.pack(candidate="agent_wrong.py"))
        self.assertEqual(rep["verdict"], "REGRESSION")
        self.assertEqual(code, 1)

    def test_unchanged_control_has_no_false_regression(self):
        code, rep, _ = self.run_gate(self.pack())
        self.assertEqual(rep["verdict"], "NO_REGRESSION_DETECTED")
        self.assertEqual(code, 0)
        self.assertIn("not a release approval", " ".join(rep["reasons"]))

    def test_unreviewed_human_case_requires_review(self):
        code, rep, _ = self.run_gate(self.pack(cases=[case("C1", "human", rubric="tone")]))
        self.assertEqual(rep["verdict"], "REVIEW_REQUIRED")
        self.assertEqual(code, 2)

    def test_reviewed_human_case_resolves(self):
        rev = {"decisions": {"C1": {"decision": "PASS", "reviewer": "itay",
                                    "evidence": "reply draft reviewed 2026-11-16", "at": "2026-11-16T22:30:00Z"}}}
        code, rep, _ = self.run_gate(self.pack(cases=[case("C1", "human", rubric="tone")]), reviews=rev)
        self.assertEqual(rep["verdict"], "NO_REGRESSION_DETECTED")
        self.assertEqual(code, 0)

    def test_inconsistent_runs_are_unresolved(self):
        # three runs, the agent alternates between right and wrong answers
        flaky = os.path.join(FIX, "agent_flaky.py")
        p = self.pack(candidate="agent_flaky.py", runs=3)
        code, rep, _ = self.run_gate(p)
        self.assertIn(rep["verdict"], ("REGRESSION", "REVIEW_REQUIRED"))
        self.assertNotEqual(code, 0)


class TestEvidence(GateCase):
    def test_evidence_rows_match_planned_executions(self):
        code, rep, _ = self.run_gate(self.pack(cases=[case("C1"), case("C2")], runs=3))
        self.assertEqual(rep["executions"], 12)      # 2 cases x 2 configs x 3 runs
        self.assertEqual(rep["expected_executions"], 12)
        path = os.path.join(self.out, "results.jsonl")
        self.assertTrue(os.path.exists(path))
        with open(path, encoding="utf-8") as f:
            lines = [json.loads(l) for l in f if l.strip()]
        self.assertEqual(len(lines), 12)
        for r in lines:
            for field in ("config", "case", "run", "verdict", "exit", "why", "ms"):
                self.assertIn(field, r)

    def test_contract_failure_writes_a_report(self):
        p = self.pack()
        p["runs"] = 0
        code, rep, _ = self.run_gate(p)
        self.assertEqual(code, EXIT["INFRA_FAILURE"])
        self.assertTrue(os.path.exists(os.path.join(self.out, "report.md")))


if __name__ == "__main__":
    unittest.main(verbosity=2)
