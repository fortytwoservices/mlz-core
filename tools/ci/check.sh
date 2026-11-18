#!/usr/bin/env bash
# check.sh <role>: checks the customer's YAML and works out what the roots read, into .mlz/.
# Prints the stacks to plan and apply as JSON: one per landing zone for vending, the role otherwise. On a pull
# request or a push, only the stacks this change touches (below); every stack on the daily check and a manual run.
# shellcheck source=tools/ci/lib.sh
source "$(dirname "$0")/lib.sh"
role=$1
mkdir -p "$WORK"

# Every stack the settings make, as JSON, with the configs the roots read written to .mlz/.
every_stack() {
  set -e # bash clears errexit in a command substitution: a failed render or check must fail it, and the script
  case $role in
    governance)
      python3 "$CORE/governance/scripts/render_library.py" --customer customer.yaml --out "$WORK/rendered" >&2
      echo '["governance"]'
      ;;
    platform)
      python3 -m mlz platform --repo . --out "$WORK/platform.json" >&2
      # As the starter comes, no connectivity file has a setting: the platform waits and plans nothing. On a first
      # deployment governance's contract then merges itself, and the pull request that writes the hub is checked against it.
      if [[ ! -f $WORK/platform.json ]]; then
        echo "::notice::No connectivity file has a setting yet (connectivity/): the platform waits for one; nothing is planned or changed." >&2
        echo '[]'
        exit 0
      fi
      # The rules stack runs with or without firewall-rules/: every hub's platform group holds the rules mlz-core
      # requires, and deleting the last rules file must take its rules out of Azure, not leave them behind.
      python3 -m mlz firewall --repo . --platform-config "$WORK/platform.json" --out "$WORK/firewall.json" >&2
      # Until vending publishes its first contract, the landing zones' rules have no sources, so the rules
      # stack stays out of the run: on a first deployment the hub goes first, and a contract gone missing from
      # a platform already deployed never plans their rules away. The files are still checked above.
      # Management's and identity's networks run every time, built or not, so turning one off takes it out
      # of Azure. They apply after the hub, which they peer with, and before the rules.
      if [[ -f _generated/vending.yaml ]]; then
        echo '["connectivity","management","identity","firewall-rules"]'   # in this order: the rules need the hub's policy
      else
        echo "::notice::No vending contract yet (_generated/vending.yaml): the firewall rules stack waits for it; nothing in it is planned or changed." >&2
        echo '["connectivity","management","identity"]'
      fi
      ;;
    vending)
      # Vending works everything out from governance's and platform's contracts. Until both are here, it waits
      # and plans nothing: on a first deployment it goes last, and its contract pull requests merge themselves
      # meanwhile; a contract gone missing from a vending already deployed never plans its landing zones away.
      for contract in governance platform; do
        if [[ ! -f _generated/$contract.yaml ]]; then
          echo "::notice::No $contract contract yet (_generated/$contract.yaml): vending waits for it; nothing is planned or changed." >&2
          echo '[]'
          exit 0
        fi
      done
      python3 -m mlz allocate --repo . --check >&2
      python3 -m mlz vending --repo . --out "$WORK/vending" >&2
      python3 -c 'import json, pathlib, sys; print(json.dumps(sorted(p.stem for p in pathlib.Path(sys.argv[1]).glob("*.json"))))' "$WORK/vending"
      ;;
    *) echo "unknown role $role" >&2; exit 1 ;;
  esac
}

stacks=$(every_stack) # its own assignment, so that a failed check fails the script

# Plan only what this change touches: the stacks whose rendered config differs from the base commit's, rendered
# here with the same mlz-core. The base is the commit before: on a pull request, its merge commit's first parent,
# the base branch; on a push, the commit main was at (a push of several commits plans everything). A changed
# pipeline file (a new mlz-core release), a base that does not check, the daily check and a manual run plan
# every stack (docs/pipelines.md).
every() {
  echo "::notice::Planning every stack: $1." >&2
  echo "$stacks"
  exit 0
}
case ${GITHUB_EVENT_NAME:-} in
  pull_request | push) ;;
  *) echo "$stacks"; exit 0 ;;
esac
[[ $stacks == '[]' ]] && { echo "$stacks"; exit 0; }
base=$(git rev-parse -q --verify 'HEAD^1' || true)
if [[ -z $base ]]; then every "the commit before this one is not here"; fi
if [[ $GITHUB_EVENT_NAME == push && $base != "${EVENT_BEFORE:-}" ]]; then every "this push brings more than one commit"; fi
if ! git diff --quiet "$base" HEAD -- .github/workflows/; then every "the pipeline changed (a new mlz-core release, for one)"; fi
copy=${RUNNER_TEMP:-$WORK}/mlz-base
rm -rf "$copy"
git worktree prune # a copy an earlier run left on this runner is gone from disk, and must go from git's list too
git worktree add -q --detach "$copy" "$base"
ln -s "$CORE" "$copy/.mlz-core"
if ! (cd "$copy" && GITHUB_EVENT_NAME='' "$CORE/tools/ci/check.sh" "$role" > "$WORK/base-stacks.json" 2> "$WORK/base-check.log"); then
  every "the base ${base:0:7} does not check with this release ($WORK/base-check.log)"
fi
python3 -m mlz stacks --role "$role" --stacks "$stacks" --base "$copy"
