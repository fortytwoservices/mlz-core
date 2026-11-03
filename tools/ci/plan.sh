#!/usr/bin/env bash
# plan.sh <role> <stack>: a read-only plan. The plan identity can only read the state, and the backend
# writes an empty state for a key it has not seen, so this plans against a downloaded copy of the state
# with a local backend. Writes, in .mlz/: plan-<stack>.txt (the full plan), summary-<stack>.md (for
# people) and changes-<stack>.txt (what the apply must match). Exit 2 means changes.
# shellcheck source=tools/ci/lib.sh
source "$(dirname "$0")/lib.sh"
role=$1 stack=$2
root=$(root_of "$role")
key=$stack.tfstate
copy=$WORK/state/$key
mkdir -p "$WORK/state"
blob=(--auth-mode login --account-name "$TFSTATE_STORAGE_ACCOUNT" --container-name "$TFSTATE_CONTAINER" --name "$key" --only-show-errors)
if [[ $(az storage blob exists "${blob[@]}" --query exists -o tsv) == true ]]; then
  az storage blob download "${blob[@]}" --file "$copy" >/dev/null
fi
printf 'terraform {\n  backend "local" {\n    path = "%s"\n  }\n}\n' "$copy" > "$root/backend_override.tf"
terraform -chdir="$root" init -reconfigure -input=false -no-color >/dev/null
set +e
# shellcheck disable=SC2046
terraform -chdir="$root" plan -input=false -no-color -lock=false -detailed-exitcode $(vars_for "$role" "$stack") -out="$WORK/$stack.tfplan"
status=$?
set -e
if [[ $status != 1 ]]; then
  terraform -chdir="$root" show -no-color "$WORK/$stack.tfplan" > "$WORK/plan-$stack.txt"
  terraform -chdir="$root" show -json "$WORK/$stack.tfplan" > "$WORK/$stack.json"
  python3 "$CORE/tools/ci/summary.py" "$WORK/$stack.json" "$stack" "$WORK/summary-$stack.md" "$WORK/changes-$stack.txt"
  rm "$WORK/$stack.json" "$WORK/$stack.tfplan"
fi
exit $status
