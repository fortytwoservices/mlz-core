#!/usr/bin/env bash
# comment.sh <stack> <plan exit code> [<role>]: puts the stack's plan on the pull request, in one comment per stack
# that later runs update. With a role (plans.sh's; a workload repo's runs give none), what changed in the stack's
# settings against the base branch comes first (mlz diff).
# shellcheck source=tools/ci/lib.sh
source "$(dirname "$0")/lib.sh"
stack=$1 status=$2 role=${3:-}
marker="<!-- mlz-plan:$stack -->"
run="$GITHUB_SERVER_URL/$GITHUB_REPOSITORY/actions/runs/$GITHUB_RUN_ID"
settings=""
if [[ -n $role ]]; then
  # Its own failure leaves the plan to be posted without it.
  settings=$(python3 -m mlz diff --role "$role" --stack "$stack") || settings="(The settings' difference could not be worked out; the run's log says why.)"
fi
{
  echo "$marker"
  if [[ -n $settings ]]; then
    echo "$settings"
    echo
  fi
  if [[ $status == 1 ]]; then
    echo "**$stack**: the plan failed; see [the run]($run)."
  else
    cat "$WORK/summary-$stack.md"
    echo
    echo "The full plan is attached to [the run]($run)."
  fi
} > "$WORK/comment-$stack.md"
# Its own earlier comment only (the run's token posts as github-actions[bot]): never one someone else wrote with the marker.
existing=$(gh api "repos/$GITHUB_REPOSITORY/issues/$PR_NUMBER/comments" --paginate \
  --jq ".[] | select(.user.login == \"github-actions[bot]\" and (.body | startswith(\"$marker\"))) | .id" | head -1)
if [[ -n $existing ]]; then
  gh api -X PATCH "repos/$GITHUB_REPOSITORY/issues/comments/$existing" -F body=@"$WORK/comment-$stack.md" >/dev/null
else
  gh api -X POST "repos/$GITHUB_REPOSITORY/issues/$PR_NUMBER/comments" -F body=@"$WORK/comment-$stack.md" >/dev/null
fi
