"""merge.py: merges a contract pull request (from tools/ci/publish.py or mlz install publish) once this run has checked
and planned it. A person's merge would add no control: the run on main that the merge starts still waits for this
role's reviewer before it applies. A contract this run did not accept stays open, so main only ever gets one its own
run has checked. Needs GH_TOKEN for an app that may write to this repo, GITHUB_REPOSITORY, PR_NUMBER, HEAD_SHA (the
commit this run checked) and BASE_REF (the branch it merges into). The branch stays: publish.py starts it again from
main for the next contract, so deleting it here could take a newer one with it.

Always exits 0 once it has looked: a pull request it leaves open is a person's to merge, and the step says why.
"""

from __future__ import annotations

import re
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))  # tools/: run as a script, as role.yml does

from ci.gh import ConfigError, Pipeline, env  # noqa: E402

GENERATED = re.compile(r"^_generated/[a-z-]+\.yaml$")
ATTEMPTS, PAUSE = 3, 10


def refusal(error: ConfigError) -> str:
    """What GitHub answered, as gh printed it."""
    return str(error).split(": ", 1)[-1]


def merge(github: Pipeline, say=print, sleep=time.sleep) -> int:
    repo, number, head, base = env("GITHUB_REPOSITORY"), int(env("PR_NUMBER")), env("HEAD_SHA"), env("BASE_REF")
    # Contracts only, and the IP plan and names that vending's run commits to them: anything else waits for a person.
    # A renamed file counts by both names, so a file moved into _generated/ is not taken from elsewhere unseen.
    names = [name for item in github.pull_files(repo, number) for name in (item["filename"], item.get("previous_filename")) if name]
    others = [name for name in names if not GENERATED.match(name)]
    if others:
        say(f"::notice::This pull request changes more than _generated/, so a person merges it: {' '.join(others)} ")
        return 0

    # Squash where the repo allows it. A rebase would drop the signature GitHub put on the app's commit.
    method = "squash" if github.squash_allowed(repo) else "merge"

    # Only the commit this run checked: a newer push has a run of its own, which merges that one. The ruleset main merges
    # only a branch up to date with main (tools/mlz/install.py), so a contract that another merge left behind is brought
    # up to date first, if it is still at HEAD_SHA: the run of that merge checks it again and merges it. Another merge a
    # moment earlier moves the base, so a refusal is tried again before it is reported.
    why = ""
    for attempt in range(1, ATTEMPTS + 1):
        if github.behind_by(repo, base, head) > 0:
            try:
                github.update_branch(repo, number, head)
            except ConfigError as error:
                say(f"::warning::#{number} is behind {base} and could not be brought up to date: a newer push has a run of its own; "
                    f"otherwise a person merges {base} into it. {refusal(error)}")
                return 0
            say(f"::notice::{base} moved on since this branch was made, so the branch is now up to date with it. "
                "The run of that merge checks it again and merges it.")
            return 0
        try:
            github.merge_pull(repo, number, method, head)
        except ConfigError as error:
            why = refusal(error)
            if "HTTP 409" in why:
                say("::notice::The branch moved on since this run checked it. The run of its newer commit merges it.")
                return 0
            if attempt < ATTEMPTS:
                sleep(PAUSE)
            continue
        say(f"::notice::Merged #{number}. The run on main now plans with it, and applies once this role's reviewer approves.")
        return 0
    say(f"::warning::#{number} stays open for a person to merge: {why}")
    return 0


def main() -> int:
    try:
        return merge(Pipeline())
    except ConfigError as error:
        print(f"::error::{error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.stdout.reconfigure(line_buffering=True)  # each line reaches the job's log before a subprocess writes there (finding 19)
    sys.exit(main())
