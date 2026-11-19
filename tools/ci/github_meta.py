"""github_meta.py <file>: GitHub's meta information (api.github.com/meta), fetched with the job's token and saved to a
file, which the check reads instead of fetching it anonymously (mlz check --github-meta). The platform's check needs its
domains.actions for the runners' firewall rule (tools/mlz/firewall.py).

GitHub allows 60 anonymous requests an hour per address, and GitHub-hosted runners share theirs with other customers,
while self-hosted runners all leave through the hub firewall's one address: an anonymous fetch could fail for a reason
nobody at the customer sees. The job's token (GH_TOKEN: github.token) allows 1,000 an hour per repository (GitHub's
"Rate limits for the REST API"), and GET /meta needs no permission. A step of its own, so the check, which runs the
pull request's YAML through the loader, keeps no token in its environment. gh makes the call, as every other GitHub call
in the pipeline: no Authorization header is ever handed to another library.

Exits 0 with the file written, 1 without it: a rate limit used up says until when, and to run again then; nothing is
retried, since the limit lasts the hour.
"""

from __future__ import annotations

import datetime
import subprocess
import sys
from pathlib import Path


def parse(output: str) -> tuple[int, dict[str, str], str]:
    """gh api --include's output: the status, the headers by lower-case name, the body."""
    head, _, body = output.replace("\r\n", "\n").partition("\n\n")
    lines = head.split("\n")
    try:
        status = int(lines[0].split()[1])
    except (IndexError, ValueError):
        return 0, {}, output
    headers = {name.strip().lower(): value.strip() for name, _, value in (line.partition(":") for line in lines[1:]) if name}
    return status, headers, body


def rate_limited(status: int, headers: dict[str, str]) -> str | None:
    """When GitHub's rate limit is used up: the time it resets, in UTC; None for any other answer."""
    if status not in (403, 429) or headers.get("x-ratelimit-remaining") != "0":
        return None
    try:
        reset = datetime.datetime.fromtimestamp(int(headers["x-ratelimit-reset"]), datetime.timezone.utc)
    except (KeyError, ValueError):
        return "it resets (within the hour)"
    return reset.strftime("%H:%M UTC")


def fetch(out: Path, run=subprocess.run, say=print) -> int:
    done = run(["gh", "api", "--include", "meta"], capture_output=True, text=True)
    status, headers, body = parse(done.stdout)
    if done.returncode == 0 and status == 200:
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(body)
        say(f"GitHub's meta, read with the job's token: {out}")
        return 0
    reset = rate_limited(status, headers)
    if reset:
        say(f"::error::GitHub's API rate limit for this repository's token is used up until {reset}: GitHub's meta "
            "(api.github.com/meta), which the platform's check needs, could not be read. Run the workflow again then.")
    else:
        say(f"::error::GitHub's meta (api.github.com/meta) could not be read: {done.stderr.strip() or (body.strip() or 'no answer')}")
    return 1


if __name__ == "__main__":
    sys.exit(fetch(Path(sys.argv[1])))
