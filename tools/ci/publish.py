"""publish.py <role>: after a run on main in which nothing failed, offers the role's contract to the repos that read
it, as a pull request each. The reading repo's run on that pull request merges it once it has checked and planned it
(tools/ci/merge.py); a contract it does not accept stays open for a person. Needs GH_TOKEN for an app that may write
to those repos and MLZ_REPOSITORIES (from the bootstrap); governance and platform also need the Azure CLI signed in as
an identity that may read the state (TFSTATE_*).

Exits 0 when every reader has the contract or is offered it, and when there is nothing to offer; 1 when a reader's
open pull request could not be brought up to date (the next run on main tries again), or a call failed.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path
from typing import Callable

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))  # tools/: run as a script, as role.yml does

from ci.gh import ConfigError, Pipeline, env, work  # noqa: E402
from mlz import contract, firewall, vending  # noqa: E402

READERS = {"governance": ("platform", "vending"), "platform": ("vending",), "vending": ("platform",)}


def state_key(role: str) -> str:
    """The state of the stack whose root outputs the contract: governance's, and the platform's connectivity stack
    (tools/mlz/stacks.py)."""
    return "connectivity.tfstate" if role == "platform" else f"{role}.tfstate"


def state_contract(role: str) -> dict | None:
    """The contract as the state holds it, which is what terraform output prints, read without a Terraform run: the
    plan identity may only read the state. None when there is no state at all; a state without the output fails."""
    key = state_key(role)
    blob = ["--auth-mode", "login", "--account-name", env("TFSTATE_STORAGE_ACCOUNT"), "--container-name", env("TFSTATE_CONTAINER"), "--name", key]
    # Only stdout is read: the Azure CLI's own message about a failure stays in the log, as it did in bash.
    exists = subprocess.run(["az", "storage", "blob", "exists", *blob, "--query", "exists", "-o", "tsv"], stdout=subprocess.PIPE, text=True, check=True)
    if exists.stdout.strip() != "true":
        return None
    copy = work() / "state" / key
    copy.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(["az", "storage", "blob", "download", *blob, "--file", str(copy), "--only-show-errors"], stdout=subprocess.DEVNULL, check=True)
    return json.loads(copy.read_text())["outputs"]["contract"]["value"]


def offer(role: str, github: Pipeline, state: Callable[[str], dict | None] = state_contract, say=print) -> int:
    readers = READERS.get(role)
    if readers is None:
        return 0
    out = work() / f"{role}.yaml"
    if role == "vending":
        # Until vending has both contracts, it has planned nothing, and an empty contract would start the platform's
        # firewall rules stack before its hub exists (docs/platform.md).
        for name in ("governance", "platform"):
            if not Path(f"_generated/{name}.yaml").is_file():
                say(f"No {name} contract here yet: vending offers nothing until it has run with both.")
                return 0
        # Vending's contract is its IP plan, by landing zone: no single state to read it from.
        out.write_text(contract.render("vending", firewall.vending_contract(vending.landing_zones(Path(".")), vending.ip_plan(Path(".")))))
    else:
        value = state(role)
        if value is None:  # nothing was ever applied, as on a platform still waiting for its hub (mlz check)
            say(f"No {role} state yet: nothing has been applied, so there is no contract to offer.")
            return 0
        out.write_text(contract.render(role, value))
    text = out.read_text()

    # A run that gets here after a newer run on main must not replace the newer one's offer: only the run of main's
    # current commit publishes. The publish job also queues per role, in role.yml.
    repository, sha = env("GITHUB_REPOSITORY"), env("GITHUB_SHA")
    head = github.sha(repository, "main")
    if head != sha:
        say(f"main is at {head} now, not at this run's {sha}: the newer run offers the contract.")
        return 0
    path, branch = f"_generated/{role}.yaml", f"mlz/{role}-contract"
    run = f"{env('GITHUB_SERVER_URL')}/{repository}/actions/runs/{env('GITHUB_RUN_ID')}"
    message = f"chore: {role} contract from {repository}"
    repositories = json.loads(env("MLZ_REPOSITORIES"))

    def same(ref: str | None = None) -> bool:
        """Whether the reader has this contract at path (on main, or on ref): a file it cannot read counts as not, and
        trailing newlines do not count, as bash compared them."""
        try:
            found = github.text(repo, path, ref)
        except ConfigError:
            found = None
        return (found or "").rstrip("\n") == text.rstrip("\n")

    failed = False
    for reader in readers:
        repo = repositories[reader]
        if same():
            say(f"{repo}: {path} is up to date")
            # A pull request left open by an earlier run offers a contract that is no longer the deployed one.
            for number in github.open_pulls(repo, branch):
                github.close_pull(repo, number, f"Closed by {run}: {path} on main already matches what {role} deployed.")
                say(f"{repo}: closed #{number}, which no longer matches what {role} deployed")
            continue
        # An open pull request gets the new contract on its own branch: moved back to main, the branch would close it,
        # since a pull request with nothing to merge is closed. The reader's run may merge it meanwhile, and the repo
        # may then delete the branch; the contract then starts again from main, in a new pull request.
        open_ = next(iter(github.open_pulls(repo, branch)), None)
        if open_ is not None:
            if same(branch):
                say(f"{repo}: #{open_} already offers this {path}")
                continue
            try:
                github.put_file(repo, path, branch, text, message)
                wrote = True
            except ConfigError:
                wrote = False
            try:
                now = next(iter(github.open_pulls(repo, branch)), None)
            except ConfigError:
                say(f"::error::{repo}: could not tell whether #{open_} is still open, so its branch is left as it is; the next run on main tries again.")
                failed = True
                continue
            if now == open_:
                if wrote:
                    say(f"{repo}: offered the new {path} in #{open_}")
                else:
                    # Still open, so its branch is left as it is: reset, it would close the pull request.
                    say(f"::error::{repo}: could not write the new {path} to the branch of #{open_}; the next run on main tries again.")
                    failed = True
                continue
        github.reset_branch(repo, branch, "main")
        github.put_file(repo, path, branch, text, message)
        github.create_pull(repo, branch, "main", f"chore: {role} contract",
                           f"The {role} contract after the apply in {run}. This repo's run merges it once it has checked and planned it; "
                           "the apply on main then waits for this repo's reviewer. If the run fails, or this repo's rules need a review "
                           "first, it stays open for a person.")
        say(f"{repo}: offered the new {path}")
    return 1 if failed else 0


def main() -> int:
    try:
        return offer(sys.argv[1], Pipeline())
    except (ConfigError, subprocess.CalledProcessError, KeyError, ValueError) as error:
        print(f"::error::{error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
