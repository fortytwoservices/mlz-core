#!/usr/bin/env bash
# publish.sh <role>: after an apply, offers the role's contract to the repos that read it, as a pull
# request each, so a person sees what changed before the other role uses it. Needs GH_TOKEN for an app
# that may write to those repos, and MLZ_REPOSITORIES (from the bootstrap).
# shellcheck source=tools/ci/lib.sh
source "$(dirname "$0")/lib.sh"
role=$1
case $role in
  governance) readers="platform vending" ;;
  platform) readers="governance vending" ;;
  *) exit 0 ;;
esac
terraform -chdir="$(root_of "$role")" output -json contract | python3 -m mlz contract "$role" --out "$WORK/$role.yaml"
path=_generated/$role.yaml
branch=mlz/$role-contract
for reader in $readers; do
  repo=$(python3 -c 'import json, os, sys; print(json.loads(os.environ["MLZ_REPOSITORIES"])[sys.argv[1]])' "$reader")
  current=$(gh api "repos/$repo/contents/$path" --jq .content 2>/dev/null | base64 -d 2>/dev/null || true)
  if [[ $current == "$(cat "$WORK/$role.yaml")" ]]; then
    echo "$repo: $path is up to date"
    continue
  fi
  main=$(gh api "repos/$repo/git/ref/heads/main" --jq .object.sha)
  gh api -X POST "repos/$repo/git/refs" -f ref="refs/heads/$branch" -f sha="$main" >/dev/null 2>&1 ||
    gh api -X PATCH "repos/$repo/git/refs/heads/$branch" -f sha="$main" -F force=true >/dev/null
  sha=$(gh api "repos/$repo/contents/$path?ref=$branch" --jq .sha 2>/dev/null || true)
  gh api -X PUT "repos/$repo/contents/$path" -f message="chore: $role contract from $GITHUB_REPOSITORY" \
    -f content="$(base64 -w0 < "$WORK/$role.yaml")" -f branch="$branch" ${sha:+-f sha="$sha"} >/dev/null
  # Only an open pull request counts: the branch is reused, and gh pr view also finds merged ones.
  open=$(gh pr list --repo "$repo" --head "$branch" --base main --state open --json number --jq length)
  if [[ $open == 0 ]]; then
    gh pr create --repo "$repo" --head "$branch" --base main --title "chore: $role contract" \
      --body "The $role contract after the apply in $GITHUB_SERVER_URL/$GITHUB_REPOSITORY/actions/runs/$GITHUB_RUN_ID. Merge it to use what $role published." >/dev/null
  fi
  echo "$repo: offered the new $path"
done
