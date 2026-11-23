"""Tests for the wiring of role.yml and workload.yml around the approval input: the Enterprise path (approval left out, as
on v0.13.0) runs the steps it ran and asks for the permissions it asked for on v0.13.0, recorded in
fixtures/enterprise-v0.13.0.json; every job and step that exists for GitHub Team alone (approval: pull-request) says so
in its if; and the Team-only parts do what they must. The workflows are read with terraform's yamldecode, as mlz reads
YAML (tools/mlz/files.py), so the tests need terraform on the PATH, as the loader's tests do.

To record the baseline again (only for a release that is meant to change the Enterprise path, and with that said in its
notes): python3 -c 'from ci import test_workflows; test_workflows.record("<ref>")' from tools/.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import tempfile
import unittest
from pathlib import Path

from mlz import files

CORE = Path(__file__).resolve().parents[2]
WORKFLOWS = CORE / ".github" / "workflows"
BASELINE = Path(__file__).resolve().parent / "fixtures" / "enterprise-v0.13.0.json"
TEAM = "inputs.approval == 'pull-request'"
NAMES = ("role.yml", "workload.yml")


def workflow(name: str) -> dict:
    return files.read_yaml(WORKFLOWS / name)


def top_level(expression: str) -> str:
    """The expression with every quoted string and every parenthesised part taken out: what binds at its top."""
    without_strings = re.sub(r"'[^']*'", "''", expression)
    depth, kept = 0, []
    for char in without_strings:
        depth += char == "("
        if depth == 0:
            kept.append(char)
        depth -= char == ")"
    return "".join(kept)


def conjuncts(expression: str) -> list[str]:
    """The expression's parts joined by && at its top level: never inside parentheses or a quoted string."""
    parts, depth, quoted, start, index = [], 0, False, 0, 0
    while index < len(expression):
        char = expression[index]
        if char == "'":
            quoted = not quoted
        elif not quoted and char in "()":
            depth += 1 if char == "(" else -1
        elif not quoted and depth == 0 and expression.startswith("&&", index):
            parts.append(expression[start:index].strip())
            start = index = index + 2
            continue
        index += 1
    return parts + [expression[start:].strip()]


def team_only(condition) -> bool:
    """Whether an if holds only in Team mode: TEAM is one of its conjuncts, with no || at its top level."""
    if not isinstance(condition, str):
        return False
    text = condition.strip()
    text = text[3:-2].strip() if text.startswith("${{") and text.endswith("}}") else text
    return "||" not in top_level(text) and TEAM in conjuncts(text)


def step_key(step: dict) -> list:
    """A step as the guard compares it: what it is (its id, name or action) and when it runs."""
    return [step.get("id") or step.get("name") or "", step.get("uses") or "", step.get("if") or ""]


def shape(data: dict, enterprise: bool = True) -> dict:
    """What the Enterprise guard compares, per job: its permissions (None when the job has no permissions key), its
    environment, and its steps in order. With enterprise, the Team-only jobs and steps are left out."""
    jobs = {}
    for name, job in data["jobs"].items():
        if enterprise and team_only(job.get("if")):
            continue
        jobs[name] = {
            "permissions": job.get("permissions"),
            "environment": job.get("environment"),
            "steps": [step_key(step) for step in job.get("steps", []) if not (enterprise and team_only(step.get("if")))],
        }
    return {"permissions": data.get("permissions"), "jobs": jobs}


def record(ref: str) -> None:
    """Writes the baseline from the workflows at a git ref (see the module's docstring)."""
    baseline = {}
    for name in NAMES:
        raw = subprocess.run(["git", "-C", str(CORE), "show", f"{ref}:.github/workflows/{name}"], capture_output=True, check=True).stdout
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / name
            path.write_bytes(raw)
            baseline[name] = shape(files.read_yaml(path), enterprise=False)
    BASELINE.write_text(json.dumps(baseline, indent=2, sort_keys=True) + "\n")


def run_step(script: str, env: dict) -> subprocess.CompletedProcess:
    """A step's run: script in bash, as GitHub runs it (bash -e), with only the given environment and a GITHUB_OUTPUT."""
    with tempfile.TemporaryDirectory() as folder:
        output = Path(folder) / "output"
        output.write_text("")
        done = subprocess.run(["bash", "-e", "-c", script], env={"PATH": os.environ["PATH"], "GITHUB_OUTPUT": str(output),
                                                                   "GITHUB_REPOSITORY": "acme/mlz-platform", **env},
                              capture_output=True, text=True)
        done.output = output.read_text()
    return done


def step(data: dict, job: str, name: str) -> dict:
    return next(item for item in data["jobs"][job]["steps"] if name in (item.get("name"), item.get("id")))


class TeamOnlyTest(unittest.TestCase):
    def test_what_counts_as_team_only(self):
        self.assertTrue(team_only("inputs.approval == 'pull-request' && always() && github.event_name == 'pull_request'"))
        self.assertTrue(team_only("${{ inputs.approval == 'pull-request' && (github.event_name == 'push' || github.event_name == 'x') }}"))
        self.assertFalse(team_only("inputs.approval == 'pull-request' && github.ref == 'main' || always()"))
        self.assertFalse(team_only("inputs.approval != 'pull-request' && always()"))
        self.assertFalse(team_only("inputs.approval == 'pull-request-x'"))
        self.assertFalse(team_only(None))
        # Inside a group, negated or not, it does not bind the whole.
        self.assertFalse(team_only("!(github.event_name == 'push' && inputs.approval == 'pull-request' && success())"))
        self.assertFalse(team_only("(inputs.approval == 'pull-request' && always()) || github.event_name == 'push'"))
        self.assertFalse(team_only("github.ref == 'a && inputs.approval == 'pull-request''"))

    def test_conjuncts_split_only_at_the_top(self):
        self.assertEqual(conjuncts("a && (b && c) && !(d && e) && 'x && y'"), ["a", "(b && c)", "!(d && e)", "'x && y'"])


class EnterpriseGuardTest(unittest.TestCase):
    """With approval left out, each workflow runs the jobs and steps v0.13.0 ran, with the same permissions and
    environments: what is new exists for GitHub Team alone."""

    def test_the_enterprise_path_is_v0_13_0_s(self):
        baseline = json.loads(BASELINE.read_text())
        for name in NAMES:
            with self.subTest(workflow=name):
                now, then = shape(workflow(name)), baseline[name]
                self.assertEqual(sorted(now["jobs"]), sorted(then["jobs"]), "a job that is not Team-only was added or removed")
                for job in then["jobs"]:
                    self.assertEqual(now["jobs"][job], then["jobs"][job], f"{name}: {job} differs from v0.13.0 on Enterprise")

    def test_role_yml_still_gives_a_job_without_permissions_nothing(self):
        self.assertEqual(workflow("role.yml")["permissions"], {})
        for name, job in workflow("role.yml")["jobs"].items():
            self.assertIn("permissions", job, f"role.yml: {name} must declare its permissions")

    def test_the_workload_gate_alone_takes_the_caller_s_grant_and_signs_in_nowhere(self):
        data = workflow("workload.yml")
        # Without a top-level permissions key, a job with none of its own gets the caller's grant: only the gate may.
        self.assertNotIn("permissions", data)
        for name, job in data["jobs"].items():
            if name == "gate":
                continue
            self.assertIn("permissions", job, f"workload.yml: {name} must declare its permissions")
        gate = data["jobs"]["gate"]
        self.assertNotIn("permissions", gate)
        self.assertTrue(team_only(gate["if"]))
        self.assertNotIn("environment", gate)
        self.assertNotIn("env", gate)
        for item in gate["steps"]:
            self.assertNotIn("azure/login", item.get("uses", ""), "the gate signs in to nothing")
            if item.get("uses", "").startswith("actions/checkout@"):
                # mlz-core's own code only, never the repo's checkout, which a pull request controls.
                self.assertEqual(item["with"]["path"], ".mlz-core")
                self.assertIn("needs.plan.outputs.core-ref", item["with"]["ref"])


class ApprovalInputTest(unittest.TestCase):
    def test_both_workflows_take_it_with_enterprise_as_the_default(self):
        for name in NAMES:
            with self.subTest(workflow=name):
                # yamldecode reads the key on as YAML 1.1's true, which Terraform then writes as "true".
                found = workflow(name)["true"]["workflow_call"]["inputs"]["approval"]
                self.assertEqual((found["type"], found["required"], found["default"]), ("string", False, "environment"))

    def test_any_other_value_stops_the_run_first(self):
        sha = "a" * 40
        for name, job in (("role.yml", "check"), ("workload.yml", "plan")):
            script = step(workflow(name), job, "core")["run"]
            env = {"WORKFLOW_REF": f"fortytwoservices/mlz-core/.github/workflows/{name}@refs/tags/v0.14.0",
                   "WORKFLOW_REPOSITORY": "fortytwoservices/mlz-core", "WORKFLOW_SHA": sha}
            for value in ("environment", "pull-request"):
                with self.subTest(workflow=name, approval=value):
                    done = run_step(script, {**env, "APPROVAL": value})
                    self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
                    self.assertIn(f"ref={sha}", done.output)
            for value in ("", "pull_request", "Pull-Request", "team"):
                with self.subTest(workflow=name, approval=value):
                    done = run_step(script, {**env, "APPROVAL": value})
                    self.assertEqual(done.returncode, 1)
                    self.assertIn(f"::error::approval is '{value}' in .github/workflows/mlz.yml: it is environment", done.stdout)
                    self.assertEqual(done.output, "")


class PlannedTest(unittest.TestCase):
    def test_it_runs_on_team_pull_requests_whatever_happened_before_it(self):
        planned = workflow("role.yml")["jobs"]["planned"]
        self.assertTrue(team_only(planned["if"]))
        self.assertIn("always()", planned["if"])  # a skipped required check counts as passed: it must never be skipped
        self.assertIn("github.event_name == 'pull_request'", planned["if"])
        self.assertEqual(planned["needs"], ["check", "plan"])
        self.assertEqual(planned["permissions"], {})

    def test_it_passes_only_when_check_passed_and_every_plan_finished_or_none_was_needed(self):
        script = step(workflow("role.yml"), "planned", "Every plan of this pull request finished")["run"]
        for check, plan, passes in (("success", "success", True), ("success", "skipped", True),
                                    ("success", "failure", False), ("success", "cancelled", False),
                                    ("failure", "skipped", False), ("cancelled", "skipped", False), ("success", "", False)):
            with self.subTest(check=check, plan=plan):
                done = run_step(script, {"CHECK": check, "PLAN": plan})
                self.assertEqual(done.returncode == 0, passes, done.stdout)
                if not passes:
                    self.assertIn(f"::error::Not every plan of this pull request finished (check: {check}, plan: {plan})", done.stdout)


class StampTest(unittest.TestCase):
    def test_team_pull_request_runs_put_their_number_with_the_plans(self):
        for name, job, title in (("role.yml", "plan", "Mark the plans as the pull request's"),
                                 ("workload.yml", "plan", "Mark the plan as the pull request's")):
            with self.subTest(workflow=name):
                steps = workflow(name)["jobs"][job]["steps"]
                index = next(i for i, item in enumerate(steps) if item.get("name") == title)
                self.assertTrue(team_only(steps[index]["if"]))
                self.assertIn("github.event_name == 'pull_request'", steps[index]["if"])
                upload = steps[index + 1]
                self.assertTrue(upload["uses"].startswith("actions/upload-artifact@"))
                self.assertIn(".mlz/pull-request.txt", upload["with"]["path"].split())
                with tempfile.TemporaryDirectory() as folder:
                    done = subprocess.run(["bash", "-e", "-c", steps[index]["run"]], cwd=folder, capture_output=True, text=True,
                                          env={"PATH": os.environ["PATH"], "PR_NUMBER": "17"})
                    self.assertEqual(done.returncode, 0, done.stderr)
                    self.assertEqual(Path(folder, ".mlz", "pull-request.txt").read_text(), "17\n")


class TeamJobsTest(unittest.TestCase):
    def test_a_role_apply_is_refused_on_team_until_the_gate_is_built(self):
        steps = workflow("role.yml")["jobs"]["changed"]["steps"]
        names = [item.get("name") or item.get("id") for item in steps]
        refusal = steps[names.index("Match the plans with their pull request's")]
        self.assertLess(names.index("Match the plans with their pull request's"), names.index("changed"))
        self.assertTrue(team_only(refusal["if"]))
        done = run_step(refusal["run"], {})
        self.assertEqual(done.returncode, 1)
        self.assertIn("::error::This mlz-core release has no gate for GitHub Team yet", done.stdout)
        # apply and publish follow only a changed job that succeeded.
        role = workflow("role.yml")["jobs"]
        self.assertIn("needs.changed.result == 'success'", role["apply"]["if"])

    def test_contracts_do_not_merge_themselves_on_team(self):
        merge = workflow("role.yml")["jobs"]["merge"]["if"]
        self.assertIn("inputs.approval != 'pull-request'", [part.strip() for part in merge.split("&&")])
        self.assertNotIn("||", top_level(merge))

    def test_a_workload_apply_needs_the_gate_on_team_and_runs_as_before_on_enterprise(self):
        apply = workflow("workload.yml")["jobs"]["apply"]
        self.assertEqual(apply["needs"], ["plan", "gate"])
        condition = apply["if"]
        self.assertTrue(condition.startswith("!cancelled() && needs.plan.result == 'success'"))
        self.assertIn("(inputs.approval == 'pull-request' && needs.gate.result == 'success' "
                      "|| inputs.approval != 'pull-request' && needs.gate.result == 'skipped')", condition)
        # Every other condition of v0.13.0's apply is still there.
        for part in ("github.ref == 'refs/heads/main'", "(github.event_name == 'push' || github.event_name == 'workflow_dispatch')",
                     "needs.plan.outputs.status == '2'"):
            self.assertIn(part, condition)

    def test_the_gate_refuses_until_it_is_built(self):
        gate = workflow("workload.yml")["jobs"]["gate"]
        done = run_step(step(workflow("workload.yml"), "gate", "Match the plan with its pull request's")["run"], {})
        self.assertEqual(done.returncode, 1)
        self.assertIn("::error::This mlz-core release has no gate for GitHub Team yet", done.stdout)
        self.assertEqual(gate["needs"], "plan")


if __name__ == "__main__":
    unittest.main()
