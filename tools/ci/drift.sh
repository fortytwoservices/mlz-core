#!/usr/bin/env bash
# drift.sh: after the daily check's plans, one issue in the repo says what is not as main says. It is opened, or
# commented on, while the check fails or any stack's plan differs from main or did not finish, and closed by the
# first daily check that finds every stack as main says. Reads CHECK (the check job's result) and, after a check
# that passed, .mlz/status-<stack>.txt (plan.sh's exit status, from each plan job) for the stacks in STACKS
# (JSON, from check); a stack without one never got to plan. Needs GH_TOKEN with issues: write, GH_REPO, RUN_URL
# and ROLE.
set -euo pipefail
title="Daily check: Azure differs from main"
drifted=() unplanned=()
if [[ ${CHECK:-} != success ]]; then
  # No stacks to go by: the YAML, the contracts or GitHub's domain list stopped the check, and nothing was planned.
  unplanned+=("every stack: the check failed (\`${CHECK:-unknown}\`), so nothing was planned")
  STACKS='[]'
fi
for stack in $(jq -r '.[]' <<<"${STACKS:-[]}"); do
  status=$(cat ".mlz/status-$stack.txt" 2>/dev/null || echo none)
  case $status in
    0) ;;
    2) drifted+=("\`$stack\`") ;;
    3) unplanned+=("\`$stack\`, refused: it deletes or replaces a firewall policy") ;;
    *) unplanned+=("\`$stack\`, the plan did not finish") ;;
  esac
done

# Only an issue with exactly this title, opened by this job: a person's issue that mentions it is left alone.
open=$(gh issue list --state open --author 'app/github-actions' --search "in:title \"$title\"" --json number,title \
  --jq "map(select(.title == \"$title\")) | .[0].number // empty")
today=$(date -u +%F)

if (( ${#drifted[@]} + ${#unplanned[@]} == 0 )); then
  if [[ -n $open ]]; then
    gh issue close "$open" --comment "The daily check of $today found every stack as main says: $RUN_URL"
  fi
  exit 0
fi

{
  echo "The daily check of $today found:"
  echo
  if (( ${#drifted[@]} )); then
    joined=$(printf '%s, ' "${drifted[@]}")
    echo "- **Azure differs from main:** ${joined%, }"
  fi
  for stack in ${unplanned[@]+"${unplanned[@]}"}; do # empty is not unset, also in bash 3
    echo "- **No plan:** $stack"
  done
  echo
  echo "The plans and their summaries are on the run page: $RUN_URL"
  echo
  echo "To bring Azure back to main, run the workflow on main and approve the apply. To keep what Azure has,"
  echo "change main to match."
  if [[ ${ROLE:-} == platform ]]; then
    echo "A change in GitHub's own list of domains, which the private runners reach, shows here too, in the"
    echo "firewall stack."
  fi
  echo
  echo "This issue closes itself when a daily check finds every stack as main says."
} > "${RUNNER_TEMP:-/tmp}/drift.md"

if [[ -n $open ]]; then
  gh issue comment "$open" --body-file "${RUNNER_TEMP:-/tmp}/drift.md"
else
  gh issue create --title "$title" --body-file "${RUNNER_TEMP:-/tmp}/drift.md"
fi
