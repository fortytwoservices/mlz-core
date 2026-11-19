#!/usr/bin/env bash
# check.sh <role>: checks the customer's YAML and works out what the roots read, into .mlz/.
# Prints the stacks to plan and apply as JSON: one per landing zone for vending, the role otherwise. On a pull
# request or a push, only the stacks this change touches (below); every stack on the daily check and a manual run.
# shellcheck source=tools/ci/lib.sh
source "$(dirname "$0")/lib.sh"
role=$1
mkdir -p "$WORK"

# Every stack the settings make, as JSON, with the configs the roots read written to .mlz/: the checks a person runs
# with ./mlz check (mlz check, in tools/mlz/__main__.py), for the role the repo's workflow names. mlz runs from
# mlz-core's own code only, whatever the checkout holds (tools/ci/run_mlz.py).
# MLZ_GITHUB_META: a saved copy of api.github.com/meta, which role.yml's check job reads with the job's token for the
# platform (tools/ci/github_meta.py). Unset, mlz firewall fetches it anonymously, as ./mlz check does.
every_stack() {
  python3 -I "$CORE/tools/ci/run_mlz.py" check --repo . --out "$WORK" --json ${MLZ_GITHUB_META:+--github-meta "$MLZ_GITHUB_META"}
}

stacks=$(every_stack) # its own assignment, so that a failed check fails the script
# The base's configs, below, are this run's or none: never an earlier run's on the same runner.
rm -rf "$WORK/base"

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
python3 -I "$CORE/tools/ci/run_mlz.py" stacks --role "$role" --stacks "$stacks" --base "$copy"
# The base's configs, which the plan comments compare each stack's settings with (comment.sh, mlz diff). In .mlz/,
# so the run's artifact takes them to the plan jobs. The folder is made even when the base has none (the platform
# starter, before its first hub): then every stack is new. Its marker keeps it through the artifact, which drops an
# empty folder.
mkdir -p "$WORK/base"
echo "The base commit's configs, for the plan comments: tools/ci/check.sh, mlz diff." > "$WORK/base/README"
for path in platform.json firewall.json vending rendered/config.json; do
  if [[ -e $copy/.mlz/$path ]]; then
    mkdir -p "$(dirname "$WORK/base/$path")"
    cp -R "$copy/.mlz/$path" "$WORK/base/$path"
  fi
done
