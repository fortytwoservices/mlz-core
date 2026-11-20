"""check.py <role>: checks the customer's YAML and works out what the roots read, into .mlz/. Prints the stacks to plan
and apply as JSON: one per landing zone for vending, the role otherwise. On a pull request or a push, only the stacks
this change touches (below); every stack on the daily check and a manual run. Was check.sh, whose exit codes, output
and messages it keeps.

The checks are the ones a person runs with ./mlz check (mlz check, in tools/mlz/__main__.py), for the role the repo's
workflow names. MLZ_GITHUB_META: a saved copy of api.github.com/meta, which role.yml's check job reads with the job's
token for the platform (tools/ci/github_meta.py). Unset, mlz firewall fetches it anonymously, as ./mlz check does.

Plan only what this change touches: the stacks whose rendered config differs from the base commit's, rendered here
with the same mlz-core. The base is the commit before: on a pull request, its merge commit's first parent, the base
branch; on a push, the commit main was at (EVENT_BEFORE; a push of several commits plans everything). A changed
pipeline file (a new mlz-core release), a changed replan.txt, a base that does not check, the daily check and a manual
run plan every stack (docs/pipelines.md). replan.txt is the way to plan every stack in a pull request, which on GitHub
Team is the only approval an apply has (tools/ci/approved.py): after drift, or an apply that failed half way.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))  # tools/: run as a script, as role.yml does

from ci.run_mlz import mlz  # noqa: E402

# Any change to it plans every stack, on the pull request and on main alike (above).
REPLAN = "replan.txt"
# The base's configs, which the plan comments compare each stack's settings with (comment.py, mlz diff).
BASE_CONFIGS = ("platform.json", "firewall.json", "vending", "rendered/config.json")


def git(*args: str) -> subprocess.CompletedProcess:
    return subprocess.run(["git", *args], stdout=subprocess.PIPE, text=True)


def every(reason: str, stacks: str) -> int:
    print(f"::notice::Planning every stack: {reason}.", file=sys.stderr)
    print(stacks)
    return 0


def check(role: str) -> int:
    repo = Path.cwd()
    core, work = repo / ".mlz-core", repo / ".mlz"
    work.mkdir(parents=True, exist_ok=True)
    meta = os.environ.get("MLZ_GITHUB_META")
    found = subprocess.run(mlz("check", "--repo", ".", "--out", str(work), "--json", *(["--github-meta", meta] if meta else [])),
                           stdout=subprocess.PIPE, text=True)
    if found.returncode != 0:
        return found.returncode
    stacks = found.stdout.rstrip("\n")
    # The base's configs, below, are this run's or none: never an earlier run's on the same runner.
    shutil.rmtree(work / "base", ignore_errors=True)
    event = os.environ.get("GITHUB_EVENT_NAME", "")
    if event not in ("pull_request", "push") or stacks == "[]":
        print(stacks)
        return 0
    base = git("rev-parse", "-q", "--verify", "HEAD^1").stdout.strip()
    if not base:
        return every("the commit before this one is not here", stacks)
    if event == "push" and base != os.environ.get("EVENT_BEFORE", ""):
        return every("this push brings more than one commit", stacks)
    if git("diff", "--quiet", base, "HEAD", "--", ".github/workflows/").returncode != 0:
        return every("the pipeline changed (a new mlz-core release, for one)", stacks)
    if git("diff", "--quiet", base, "HEAD", "--", REPLAN).returncode != 0:
        return every(f"{REPLAN} changed", stacks)
    copy = Path(os.environ.get("RUNNER_TEMP") or work) / "mlz-base"
    shutil.rmtree(copy, ignore_errors=True)
    # A copy an earlier run left on this runner is gone from disk, and must go from git's list too.
    subprocess.run(["git", "worktree", "prune"], check=True)
    subprocess.run(["git", "worktree", "add", "-q", "--detach", str(copy), base], check=True)
    (copy / ".mlz-core").symlink_to(core)
    with open(work / "base-stacks.json", "w") as out, open(work / "base-check.log", "w") as log:
        checked = subprocess.run([sys.executable, str(core / "tools" / "ci" / "check.py"), role], cwd=copy,
                                 env={**os.environ, "GITHUB_EVENT_NAME": ""}, stdout=out, stderr=log).returncode == 0
    if not checked:
        return every(f"the base {base[:7]} does not check with this release ({work}/base-check.log)", stacks)
    sys.stdout.flush()
    changed = subprocess.run(mlz("stacks", "--role", role, "--stacks", stacks, "--base", str(copy))).returncode
    if changed != 0:
        return changed
    # In .mlz/, so the run's artifact takes the base's configs to the plan jobs. The folder is made even when the base
    # has none (the platform starter, before its first hub): then every stack is new. Its marker keeps it through the
    # artifact, which drops an empty folder.
    (work / "base").mkdir(parents=True, exist_ok=True)
    (work / "base" / "README").write_text("The base commit's configs, for the plan comments: tools/ci/check.py, mlz diff.\n")
    for path in BASE_CONFIGS:
        source, target = copy / ".mlz" / path, work / "base" / path
        if source.exists():
            target.parent.mkdir(parents=True, exist_ok=True)
            if source.is_dir():
                shutil.copytree(source, target, dirs_exist_ok=True)
            else:
                shutil.copyfile(source, target)
    return 0


if __name__ == "__main__":
    sys.stdout.reconfigure(line_buffering=True)  # each line reaches the job's log before a subprocess writes there (finding 19)
    try:
        sys.exit(check(sys.argv[1]))
    except subprocess.CalledProcessError as error:
        sys.exit(error.returncode or 1)
