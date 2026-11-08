#!/usr/bin/env bash
# publish.sh <role>: after a run on main in which nothing failed, offers the role's contract to the repos
# that read it, as a pull request each, so a person sees what changed before the other role uses it. Needs
# GH_TOKEN for an app that may write to those repos and MLZ_REPOSITORIES (from the bootstrap); governance and
# platform also need the Azure CLI signed in as an identity that may read the state (TFSTATE_*).
# shellcheck source=tools/ci/lib.sh
source "$(dirname "$0")/lib.sh"
role=$1
case $role in
  governance) readers="platform vending" ;;
  platform) readers="governance vending" ;;
  vending) readers="platform" ;;
  *) exit 0 ;;
esac
if [[ $role == vending ]]; then
  # Vending's contract is its IP plan, by landing zone: no single state to read it from.
  python3 -m mlz vending-contract --repo . --out "$WORK/$role.yaml"
else
  # The contract as the state holds it, which is what terraform output prints, read without a Terraform
  # run: the plan identity may only read the state. A state without the output fails here, loudly.
  mkdir -p "$WORK/state"
  az storage blob download --auth-mode login --account-name "$TFSTATE_STORAGE_ACCOUNT" \
    --container-name "$TFSTATE_CONTAINER" --name "$role.tfstate" --file "$WORK/state/$role.tfstate" --only-show-errors >/dev/null
  python3 -c 'import json, sys; print(json.dumps(json.load(open(sys.argv[1]))["outputs"]["contract"]["value"]))' "$WORK/state/$role.tfstate" |
    python3 -m mlz contract "$role" --out "$WORK/$role.yaml"
fi
path=_generated/$role.yaml
branch=mlz/$role-contract
for reader in $readers; do
  repo=$(python3 -c 'import json, os, sys; print(json.loads(os.environ["MLZ_REPOSITORIES"])[sys.argv[1]])' "$reader")
  current=$(gh api "repos/$repo/contents/$path" --jq .content 2>/dev/null | base64 -d 2>/dev/null || true)
  if [[ $current == "$(cat "$WORK/$role.yaml")" ]]; then
    echo "$repo: $path is up to date"
    # A pull request left open by an earlier run offers a contract that is no longer the deployed one.
    stale=$(gh pr list --repo "$repo" --head "$branch" --base main --state open --json number --jq '.[].number')
    for number in $stale; do
      gh pr close "$number" --repo "$repo" --delete-branch \
        --comment "Closed by $GITHUB_SERVER_URL/$GITHUB_REPOSITORY/actions/runs/$GITHUB_RUN_ID: $path on main already matches what $role deployed." >/dev/null
      echo "$repo: closed #$number, which no longer matches what $role deployed"
    done
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
