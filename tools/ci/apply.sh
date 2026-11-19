#!/usr/bin/env bash
# apply.sh <role> <stack>: plans against the stack's state in the account, as the apply identity, and
# applies only if the plan makes exactly the changes in the plan the reviewer saw, to the same values
# (.mlz/reviewed/changes-<stack>.txt, from the plan job of the same run). Otherwise it stops: Azure or the
# state changed in between, and the run must start again.
# shellcheck source=tools/ci/lib.sh
source "$(dirname "$0")/lib.sh"
role=$1 stack=$2
root=$(root_of "$role" "$stack")
# Its refusals are apply_guard.py's, each with a test: no reviewed plan, a change main no longer says (stand down), a
# state another run changed, a plan that differs from the reviewed one.
set +e
python3 "$CORE/tools/ci/apply_guard.py" before "$role" "$stack"
verdict=$?
set -e
case $verdict in 0) ;; 2) exit 0 ;; *) exit 1 ;; esac
terraform -chdir="$root" init -reconfigure -input=false -no-color \
  -backend-config="storage_account_name=$TFSTATE_STORAGE_ACCOUNT" \
  -backend-config="container_name=$TFSTATE_CONTAINER" \
  -backend-config="key=$stack.tfstate"
# shellcheck disable=SC2046
terraform -chdir="$root" plan -input=false -no-color $(vars_for "$role" "$stack") -out="$WORK/plans/$stack.tfplan" >/dev/null
# The state must still be the one the reviewed plan started from, read after this plan was made (apply_guard.py). Its
# own assignment, so that a failed read stops the apply.
now_version=$(terraform -chdir="$root" state pull | python3 "$CORE/tools/ci/state_version.py")
python3 "$CORE/tools/ci/apply_guard.py" state "$stack" "$now_version"
terraform -chdir="$root" show -json "$WORK/plans/$stack.tfplan" > "$WORK/plans/$stack.json"
if ! python3 "$CORE/tools/ci/summary.py" "$WORK/plans/$stack.json" "$stack" "$WORK/apply-summary-$stack.md" "$WORK/apply-changes-$stack.txt"; then
  echo "::error::The plan is refused; nothing is applied."
  cat "$WORK/apply-summary-$stack.md"
  exit 1
fi
python3 "$CORE/tools/ci/apply_guard.py" matches "$stack"
terraform -chdir="$root" apply -input=false -no-color "$WORK/plans/$stack.tfplan"
