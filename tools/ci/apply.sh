#!/usr/bin/env bash
# apply.sh <role> <stack>: plans against the stack's state in the account, as the apply identity, and
# applies only if the plan makes exactly the changes in the plan the reviewer saw, to the same values
# (.mlz/reviewed/changes-<stack>.txt, from the plan job of the same run). Otherwise it stops: Azure or the
# state changed in between, and the run must start again.
# shellcheck source=tools/ci/lib.sh
source "$(dirname "$0")/lib.sh"
role=$1 stack=$2
root=$(root_of "$role")
reviewed=$WORK/reviewed/changes-$stack.txt
[[ -f $reviewed ]] || { echo "::error::No reviewed plan for $stack in this run; nothing is applied."; exit 1; }
terraform -chdir="$root" init -reconfigure -input=false -no-color \
  -backend-config="storage_account_name=$TFSTATE_STORAGE_ACCOUNT" \
  -backend-config="container_name=$TFSTATE_CONTAINER" \
  -backend-config="key=$stack.tfstate"
# shellcheck disable=SC2046
terraform -chdir="$root" plan -input=false -no-color $(vars_for "$role" "$stack") -out="$WORK/plans/$stack.tfplan" >/dev/null
terraform -chdir="$root" show -json "$WORK/plans/$stack.tfplan" > "$WORK/plans/$stack.json"
python3 "$CORE/tools/ci/summary.py" "$WORK/plans/$stack.json" "$stack" "$WORK/apply-summary-$stack.md" "$WORK/apply-changes-$stack.txt"
if ! diff -u "$reviewed" "$WORK/apply-changes-$stack.txt" > "$WORK/difference-$stack.txt"; then
  echo "::error::The plan differs from the one the reviewer saw; nothing is applied. Run the pipeline again for a fresh plan."
  cat "$WORK/difference-$stack.txt"
  exit 1
fi
echo "The plan matches the reviewed one: $(wc -l < "$reviewed" | tr -d ' ') change(s)."
terraform -chdir="$root" apply -input=false -no-color "$WORK/plans/$stack.tfplan"
