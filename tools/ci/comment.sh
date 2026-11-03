#!/usr/bin/env bash
# comment.sh <stack> <plan exit code>: puts the stack's plan on the pull request, in one comment per stack
# that later runs update.
# shellcheck source=tools/ci/lib.sh
source "$(dirname "$0")/lib.sh"
stack=$1 status=$2
marker="<!-- mlz-plan:$stack -->"
summary=$(grep -E '^(Plan:|No changes\.)' "$WORK/plan-$stack.txt" | head -1 || true)
[[ $status == 1 ]] && summary="The plan failed; see the job log."
{
  echo "$marker"
  echo "**$stack**: ${summary:-no summary}"
  echo
  echo "<details><summary>Plan</summary>"
  echo
  echo '```'
  head -c 60000 "$WORK/plan-$stack.txt"
  echo '```'
  echo "</details>"
} > "$WORK/comment-$stack.md"
existing=$(gh api "repos/$GITHUB_REPOSITORY/issues/$PR_NUMBER/comments" --paginate --jq ".[] | select(.body | startswith(\"$marker\")) | .id" | head -1)
if [[ -n $existing ]]; then
  gh api -X PATCH "repos/$GITHUB_REPOSITORY/issues/comments/$existing" -F body=@"$WORK/comment-$stack.md" >/dev/null
else
  gh api -X POST "repos/$GITHUB_REPOSITORY/issues/$PR_NUMBER/comments" -F body=@"$WORK/comment-$stack.md" >/dev/null
fi
