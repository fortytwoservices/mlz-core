#!/usr/bin/env bash
# stale-comments.sh: on a pull request, marks the plan comment of every stack this commit does not plan, its config
# being as on the base again (check.sh), so that no earlier plan or failure stays on the pull request as if it still
# held. Needs GH_TOKEN, PR_NUMBER and STACKS (what this run plans, as JSON).
# shellcheck source=tools/ci/lib.sh
source "$(dirname "$0")/lib.sh"
gh api "repos/$GITHUB_REPOSITORY/issues/$PR_NUMBER/comments" --paginate \
  --jq '.[] | select(.body | startswith("<!-- mlz-plan:")) | "\(.id) \(.body | capture("^<!-- mlz-plan:(?<stack>[^ ]+) -->").stack)"' |
  while read -r id stack; do
    if python3 -c 'import json, sys; sys.exit(sys.argv[1] in json.loads(sys.argv[2]))' "$stack" "$STACKS"; then
      printf '<!-- mlz-plan:%s -->\n**%s**: not planned on this commit: its config is as on the base branch, so this pull request does not change it.\n' \
        "$stack" "$stack" > "$WORK/comment-$stack.md"
      gh api -X PATCH "repos/$GITHUB_REPOSITORY/issues/comments/$id" -F body=@"$WORK/comment-$stack.md" >/dev/null
      echo "$stack: its earlier plan comment is marked as not planned on this commit"
    fi
  done
