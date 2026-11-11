#!/usr/bin/env bash
# merge.sh: merges a contract pull request (from tools/ci/publish.sh or mlz install publish) once this run has
# checked and planned it. A person's merge would add no control: the run on main that the merge starts still
# waits for this role's reviewer before it applies. A contract this run did not accept stays open, so main
# only ever gets one its own run has checked. Needs GH_TOKEN for an app that may write to this repo,
# GITHUB_REPOSITORY, PR_NUMBER, and HEAD_SHA (the commit this run checked). The branch stays: publish.sh
# starts it again from main for the next contract, so deleting it here could take a newer one with it.
# shellcheck source=tools/ci/lib.sh
source "$(dirname "$0")/lib.sh"

# Contracts only, and the IP plan that vending's run commits to them: anything else waits for a person. A
# renamed file counts by both names, so a file moved into _generated/ is not taken from elsewhere unseen.
others=$(gh api --paginate "repos/$GITHUB_REPOSITORY/pulls/$PR_NUMBER/files" --jq '.[] | .filename, (.previous_filename // empty) | select(test("^_generated/[a-z-]+\\.yaml$") | not)')
if [[ -n $others ]]; then
  echo "::notice::This pull request changes more than _generated/, so a person merges it: $(tr '\n' ' ' <<< "$others")"
  exit 0
fi

# Squash where the repo allows it. A rebase would drop the signature GitHub put on the app's commit.
method=merge
[[ $(gh api "repos/$GITHUB_REPOSITORY" --jq .allow_squash_merge) == true ]] && method=squash

# Only the commit this run checked: a newer push has a run of its own, which merges that one. Another
# contract merged a moment earlier moves the base, so a refusal is tried again before it is reported.
for attempt in 1 2 3; do
  if refusal=$(gh api -X PUT "repos/$GITHUB_REPOSITORY/pulls/$PR_NUMBER/merge" -f merge_method="$method" -f sha="$HEAD_SHA" 2>&1 >/dev/null); then
    echo "::notice::Merged #$PR_NUMBER. The run on main now plans with it, and applies once this role's reviewer approves."
    exit 0
  fi
  if [[ $refusal == *"HTTP 409"* ]]; then
    echo "::notice::The branch moved on since this run checked it. The run of its newer commit merges it."
    exit 0
  fi
  if (( attempt < 3 )); then sleep 10; fi
done
echo "::warning::#$PR_NUMBER stays open for a person to merge: $refusal"
