#!/usr/bin/env bash
# plans.sh <role> <stack> [<stack> ...]: plans a group of stacks one after the other, in one job (tools/mlz/stacks.py's
# batches), so a hundred landing zones hold a runner for a few jobs instead of queueing a hundred. For each stack, as
# one job per stack did before: the plan (plan.sh), its summary on the run page, its plan comment on a pull request,
# and on the daily check its status for drift.sh (.mlz/status-<stack>.txt: the plan's exit code, or none when the
# job stopped before planning it). Exits 1 when any plan failed or was refused, or the daily check found drift; every
# stack is planned either way. Needs GITHUB_EVENT_NAME, and on a pull request what comment.sh needs.
# shellcheck source=tools/ci/lib.sh
source "$(dirname "$0")/lib.sh"
role=$1
shift
failed=()
for stack in "$@"; do
  set +e
  "$CORE/tools/ci/plan.sh" "$role" "$stack"
  status=$?
  set -e
  # plan.sh's own codes are 0, 2 and 3, each with a summary. Anything else, or one without its summary, stopped early
  # (a failed sign-in, say): a failed plan, never drift, and never a reason to skip the stacks after it.
  if [[ ! $status =~ ^[023]$ || ! -f $WORK/summary-$stack.md ]]; then status=1; fi
  [[ ${GITHUB_EVENT_NAME:-} == schedule ]] && echo "$status" > "$WORK/status-$stack.txt"
  if [[ $status != 1 ]]; then
    cat "$WORK/summary-$stack.md" >> "${GITHUB_STEP_SUMMARY:-/dev/null}"
  fi
  if [[ ${GITHUB_EVENT_NAME:-} == pull_request ]]; then
    "$CORE/tools/ci/comment.sh" "$stack" "$status" || echo "::warning::$stack: its plan could not be put on the pull request; the run page has it."
  fi
  case $status in
    1) echo "::error::$stack: the plan failed."; failed+=("$stack") ;;
    3) echo "::error::$stack: the plan is refused: it deletes or replaces a firewall policy, and every rule in it would go too. Its plan says which."; failed+=("$stack") ;;
    2) if [[ ${GITHUB_EVENT_NAME:-} == schedule ]]; then echo "::error::$stack: drift: Azure differs from main. Its plan shows how."; failed+=("$stack"); fi ;;
  esac
done
if [[ ${#failed[@]} -gt 0 ]]; then
  echo "Planned $# stack(s); not as they should be: ${failed[*]}."
  exit 1
fi
echo "Planned $# stack(s)."
