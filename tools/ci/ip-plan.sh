#!/usr/bin/env bash
# ip-plan.sh: on a vending pull request, brings the IP plan up to date and commits it to the pull request's
# branch, so nobody runs the allocator by hand. It allocates on the checkout, which is the pull request
# merged into its base: blocks the base has given out since the branch was made are taken into account, so
# two pull requests never get the same block (the check on main still stops one that does).
#
# Exits 0 when the plan is already up to date, and 1 when it changed the branch or could not: this run then
# stops, and the run of the new commit goes on. Needs GH_TOKEN for an app that may write to this repo and
# update its pull requests, GITHUB_REPOSITORY, PR_NUMBER, HEAD_REF and HEAD_SHA (the pull request's branch
# and the commit this run checked), and BASE_SHA (its base).
# shellcheck source=tools/ci/lib.sh
source "$(dirname "$0")/lib.sh"
path=_generated/ip-plan.yaml
message="chore: IP plan for this pull request"

# The addresses come from the platform's contract: without it there is nothing to give out yet, and the
# check says vending waits for it.
if [[ ! -f _generated/platform.yaml ]]; then
  echo "No platform contract yet (_generated/platform.yaml): no addresses to give out."
  exit 0
fi

# Writes the plan, or nothing at all and fails when anything is wrong (a conflict, a landing zone that does
# not fit): that failure is the check's own, with every problem listed.
python3 -m mlz allocate --repo . >&2
if [[ -z $(git status --porcelain -- "$path") ]]; then
  echo "The IP plan is up to date."
  exit 0
fi
git diff -- "$path" >&2 || true

# The plan this step committed must come out the same on the next run. If it does not, stop rather than
# commit again and again.
last=$(gh api "repos/$GITHUB_REPOSITORY/commits/$HEAD_SHA" --jq .commit.message)
if [[ $last == "$message" ]]; then
  echo "::error::The IP plan changed again after it was committed to this pull request. Run python3 -m mlz allocate --repo . on the branch and commit the result."
  exit 1
fi

# The plan written here includes what the base gave out since the branch was made. If the base changed the
# plan in that time, committing it onto the branch as it is would conflict with the base, and a conflicting
# pull request starts no run. So the branch is first brought up to date with its base (GitHub's update
# branch, only if it is still at HEAD_SHA); the run of that merge then commits the plan on top.
moved=$(gh api "repos/$GITHUB_REPOSITORY/compare/$HEAD_SHA...$BASE_SHA" --jq "[.files[]?.filename] | any(. == \"$path\")")
if [[ $moved == true ]]; then
  if ! gh api -X PUT "repos/$GITHUB_REPOSITORY/pulls/$PR_NUMBER/update-branch" -f expected_head_sha="$HEAD_SHA" >/dev/null; then
    echo "::error::The base changed the IP plan since this branch was made, and the branch could not be brought up to date with it. Merge the base into the branch; the next run writes the plan."
    exit 1
  fi
  echo "::notice::The base changed the IP plan since this branch was made, so the branch is now up to date with it. The run of that merge commits this pull request's IP plan."
  exit 1
fi

# One commit with the plan, made only if the branch still points at HEAD_SHA: a push since this run started
# has a run of its own, which writes the plan for it.
python3 - "$GITHUB_REPOSITORY" "$HEAD_REF" "$HEAD_SHA" "$message" "$path" > "$WORK/ip-plan-commit.json" <<'EOF'
import base64, json, sys
repository, branch, head, message, path = sys.argv[1:]
print(json.dumps({
    "query": "mutation($input: CreateCommitOnBranchInput!) { createCommitOnBranch(input: $input) { commit { oid } } }",
    "variables": {"input": {
        "branch": {"repositoryNameWithOwner": repository, "branchName": branch},
        "expectedHeadOid": head,
        "message": {"headline": message},
        "fileChanges": {"additions": [{"path": path, "contents": base64.b64encode(open(path, "rb").read()).decode()}]},
    }},
}))
EOF
if ! commit=$(gh api graphql --input "$WORK/ip-plan-commit.json" --jq .data.createCommitOnBranch.commit.oid); then
  now=$(gh api "repos/$GITHUB_REPOSITORY/pulls/$PR_NUMBER" --jq .head.sha)
  if [[ $now != "$HEAD_SHA" ]]; then
    echo "::notice::The branch moved on while this run allocated (now ${now:0:7}). The run of that commit writes the IP plan."
  else
    echo "::error::Could not commit the IP plan to $HEAD_REF."
  fi
  exit 1
fi
echo "::notice::The IP plan was out of date, so it is now committed to this pull request (${commit:0:7}). The run of that commit plans with the new addresses."
exit 1
