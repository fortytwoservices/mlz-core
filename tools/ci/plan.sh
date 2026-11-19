#!/usr/bin/env bash
# plan.sh <role> <stack>: a read-only plan. The plan identity can only read the state, and the backend
# writes an empty state for a key it has not seen, so this plans against a downloaded copy of the state
# with a local backend. Writes, in .mlz/: plan-<stack>.txt (the full plan), summary-<stack>.md (for
# people), changes-<stack>.txt (what the apply must match) and version-<stack>.txt (the state it started from). Exits 0 for no changes, 2 for changes, 3
# when summary.py refuses the plan (the summary says why), and 1 when the plan fails.
# shellcheck source=tools/ci/lib.sh
source "$(dirname "$0")/lib.sh"
role=$1 stack=$2
root=$(root_of "$role" "$stack")
key=$stack.tfstate
copy=$WORK/state/$key
mkdir -p "$WORK/state"
blob=(--auth-mode login --account-name "$TFSTATE_STORAGE_ACCOUNT" --container-name "$TFSTATE_CONTAINER" --name "$key" --only-show-errors)
# Its own assignment, so that a failed lookup stops the plan instead of planning every resource as new.
exists=$(az storage blob exists "${blob[@]}" --query exists -o tsv)
if [[ $exists == true ]]; then
  az storage blob download "${blob[@]}" --file "$copy" >/dev/null
fi
# The state this plan starts from, for the apply to refuse one another run has changed since (apply.sh).
if [[ -f $copy ]]; then python3 "$CORE/tools/ci/state_version.py" "$copy"; else echo none; fi > "$WORK/version-$stack.txt"
printf 'terraform {\n  backend "local" {\n    path = "%s"\n  }\n}\n' "$copy" > "$root/backend_override.tf"
terraform -chdir="$root" init -reconfigure -input=false -no-color >/dev/null
set +e
# shellcheck disable=SC2046
terraform -chdir="$root" plan -input=false -no-color -lock=false -detailed-exitcode $(vars_for "$role" "$stack") -out="$WORK/plans/$stack.tfplan"
status=$?
set -e
if [[ $status != 1 ]]; then
  terraform -chdir="$root" show -no-color "$WORK/plans/$stack.tfplan" > "$WORK/plan-$stack.txt"
  terraform -chdir="$root" show -json "$WORK/plans/$stack.tfplan" > "$WORK/plans/$stack.json"
  set +e
  python3 "$CORE/tools/ci/summary.py" "$WORK/plans/$stack.json" "$stack" "$WORK/summary-$stack.md" "$WORK/changes-$stack.txt"
  check=$?
  set -e
  rm "$WORK/plans/$stack.json" "$WORK/plans/$stack.tfplan"
  if [[ $check == 3 ]]; then status=3; elif [[ $check != 0 ]]; then exit 1; fi
fi
exit $status
