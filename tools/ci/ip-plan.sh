#!/usr/bin/env bash
# ip-plan.sh: on a vending pull request, brings the IP plan up to date and commits it to the pull request's
# branch, so nobody runs the allocator by hand. It allocates on the checkout, which is the pull request
# merged into main: blocks main has given out since the branch was made are taken into account, so two
# pull requests never get the same block (main's own check still stops one that does).
#
# Exits 0 when the plan is already up to date, and 1 when it committed a new one or could not: this run then
# stops, and the run of the new commit plans with the new addresses. Needs GH_TOKEN for an app that may write
# to this repo, HEAD_REF and HEAD_SHA (the pull request's branch and commit) and GITHUB_REPOSITORY.
# shellcheck source=tools/ci/lib.sh
source "$(dirname "$0")/lib.sh"
path=_generated/ip-plan.yaml
message="chore: IP plan for this pull request"

# Writes the plan, or nothing at all and fails when anything is wrong (a conflict, a landing zone that does
# not fit): that failure is the check's own, with every problem listed.
python3 -m mlz allocate --repo . >&2
if [[ -z $(git status --porcelain -- "$path") ]]; then
  echo "The IP plan is up to date."
  exit 0
fi

# The plan this step committed must come out the same on the next run. If it does not, stop rather than
# commit again and again.
last=$(gh api "repos/$GITHUB_REPOSITORY/commits/$HEAD_SHA" --jq .commit.message)
if [[ $last == "$message" ]]; then
  echo "::error::The IP plan changed again after it was committed to this pull request. Run python3 -m mlz allocate --repo . on the branch and commit the result."
  git diff -- "$path"
  exit 1
fi

# The file's current version on the branch, to replace it; none when the branch has no plan yet. Read on
# its own, so that a failed read stops here instead of looking like a new file.
if found=$(gh api "repos/$GITHUB_REPOSITORY/contents/$path?ref=$HEAD_REF" --jq .sha 2>&1); then
  sha=$found
elif [[ $found == *"(HTTP 404)"* ]]; then
  sha=""
else
  echo "::error::Could not read $path on $HEAD_REF: $found"
  exit 1
fi
commit=$(gh api -X PUT "repos/$GITHUB_REPOSITORY/contents/$path" -f message="$message" -f branch="$HEAD_REF" \
  -f content="$(base64 -w0 < "$path")" ${sha:+-f sha="$sha"} --jq .commit.sha)
git diff -- "$path" >&2 || true
echo "::notice::The IP plan was out of date, so it is now committed to this pull request (${commit:0:7}). The run of that commit plans with the new addresses."
exit 1
