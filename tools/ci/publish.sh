#!/usr/bin/env bash
# publish.sh <role>: after a run on main in which nothing failed, offers the role's contract to the repos
# that read it, as a pull request each. The reading repo's run on that pull request merges it once it has
# checked and planned it (tools/ci/merge.sh); a contract it does not accept stays open for a person. Needs
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
  # Until vending has both contracts, it has planned nothing, and an empty contract would start the platform's
  # firewall rules stack before its hub exists (docs/platform.md).
  for contract in governance platform; do
    if [[ ! -f _generated/$contract.yaml ]]; then
      echo "No $contract contract here yet: vending offers nothing until it has run with both."
      exit 0
    fi
  done
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
# A run that gets here after a newer run on main must not replace the newer one's offer: only the run of
# main's current commit publishes. The publish job also queues per role, in role.yml.
head=$(gh api "repos/$GITHUB_REPOSITORY/commits/main" --jq .sha)
if [[ $head != "$GITHUB_SHA" ]]; then
  echo "main is at $head now, not at this run's $GITHUB_SHA: the newer run offers the contract."
  exit 0
fi
path=_generated/$role.yaml
branch=mlz/$role-contract

# The open pull request from the contract branch, if any. Only an open one counts: the branch is reused.
open_pull() {
  gh pr list --repo "$1" --head "$branch" --base main --state open --json number --jq '.[0].number // empty'
}

# The contract as one commit on the branch, over whatever the branch holds at that path.
put_contract() {
  local sha
  sha=$(gh api "repos/$1/contents/$path?ref=$branch" --jq .sha 2>/dev/null || true)
  gh api -X PUT "repos/$1/contents/$path" -f message="chore: $role contract from $GITHUB_REPOSITORY" \
    -f content="$(base64 -w0 < "$WORK/$role.yaml")" -f branch="$branch" ${sha:+-f sha="$sha"} >/dev/null
}

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
  # An open pull request gets the new contract on its own branch: moved back to main, the branch would close
  # it, since a pull request with nothing to merge is closed. The reader's run may merge it meanwhile, and the
  # repo may then delete the branch; the contract then starts again from main, in a new pull request.
  open=$(open_pull "$repo")
  if [[ -n $open ]]; then
    if [[ $(gh api "repos/$repo/contents/$path?ref=$branch" --jq .content 2>/dev/null | base64 -d 2>/dev/null || true) == "$(cat "$WORK/$role.yaml")" ]]; then
      echo "$repo: #$open already offers this $path"
      continue
    fi
    if put_contract "$repo" 2>/dev/null && [[ $(open_pull "$repo") == "$open" ]]; then
      echo "$repo: offered the new $path in #$open"
      continue
    fi
  fi
  main=$(gh api "repos/$repo/git/ref/heads/main" --jq .object.sha)
  gh api -X POST "repos/$repo/git/refs" -f ref="refs/heads/$branch" -f sha="$main" >/dev/null 2>&1 ||
    gh api -X PATCH "repos/$repo/git/refs/heads/$branch" -f sha="$main" -F force=true >/dev/null
  put_contract "$repo"
  gh pr create --repo "$repo" --head "$branch" --base main --title "chore: $role contract" \
    --body "The $role contract after the apply in $GITHUB_SERVER_URL/$GITHUB_REPOSITORY/actions/runs/$GITHUB_RUN_ID. This repo's run merges it once it has checked and planned it; the apply on main then waits for this repo's reviewer. If the run fails, or this repo's rules need a review first, it stays open for a person." >/dev/null
  echo "$repo: offered the new $path"
done
