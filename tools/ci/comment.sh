#!/usr/bin/env bash
# comment.sh <stack> <plan exit code>: puts the stack's plan on the pull request, in one comment per stack
# that later runs update.
# shellcheck source=tools/ci/lib.sh
source "$(dirname "$0")/lib.sh"
stack=$1 status=$2
marker="<!-- mlz-plan:$stack -->"
run="$GITHUB_SERVER_URL/$GITHUB_REPOSITORY/actions/runs/$GITHUB_RUN_ID"
{
  echo "$marker"
  if [[ $status == 1 ]]; then
    echo "**$stack**: the plan failed; see [the run]($run)."
  else
    cat "$WORK/summary-$stack.md"
    echo
    echo "The full plan is attached to [the run]($run)."
  fi
} > "$WORK/comment-$stack.md"
existing=$(gh api "repos/$GITHUB_REPOSITORY/issues/$PR_NUMBER/comments" --paginate --jq ".[] | select(.body | startswith(\"$marker\")) | .id" | head -1)
if [[ -n $existing ]]; then
  gh api -X PATCH "repos/$GITHUB_REPOSITORY/issues/comments/$existing" -F body=@"$WORK/comment-$stack.md" >/dev/null
else
  gh api -X POST "repos/$GITHUB_REPOSITORY/issues/$PR_NUMBER/comments" -F body=@"$WORK/comment-$stack.md" >/dev/null
fi
