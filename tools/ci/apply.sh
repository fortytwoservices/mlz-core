#!/usr/bin/env bash
# apply.sh <role> <stack>: plans against the stack's state in the account, as the apply identity, and
# applies only if the plan makes exactly the changes in the plan the reviewer saw, to the same values
# (.mlz/reviewed/changes-<stack>.txt, from the plan job of the same run). Otherwise it stops: Azure or the
# state changed in between, and the run must start again.
# shellcheck source=tools/ci/lib.sh
source "$(dirname "$0")/lib.sh"
role=$1 stack=$2
root=$(root_of "$role" "$stack")
reviewed=$WORK/reviewed/changes-$stack.txt
[[ -f $reviewed ]] || { echo "::error::No reviewed plan for $stack in this run; nothing is applied."; exit 1; }
# Vending's runs on main do not queue (docs/pipelines.md). When main has moved on and its latest commit renders this
# landing zone otherwise than this run did, a newer change, or one taken back, supersedes this one: that commit's run
# plans it, and applies it if anything is left to change. So this one stands down rather than apply what main no longer
# says. MLZ_MAIN is a checkout of main's latest commit (role.yml).
if [[ $role == vending && -n ${MLZ_MAIN:-} && -d ${MLZ_MAIN:-} && $(git -C "$MLZ_MAIN" rev-parse HEAD) != "${GITHUB_SHA:-}" ]]; then
  latest=$WORK/main-vending
  if ! python3 -m mlz vending --repo "$MLZ_MAIN" --out "$latest" > "$WORK/main-vending.log" 2>&1 || ! cmp -s "$latest/$stack.json" "$WORK/vending/$stack.json"; then
    echo "::notice::main has moved on, and its latest commit changes $stack (or no longer checks with this release): that commit's run plans it and applies what is left to change. Nothing is applied here."
    exit 0
  fi
fi
terraform -chdir="$root" init -reconfigure -input=false -no-color \
  -backend-config="storage_account_name=$TFSTATE_STORAGE_ACCOUNT" \
  -backend-config="container_name=$TFSTATE_CONTAINER" \
  -backend-config="key=$stack.tfstate"
# shellcheck disable=SC2046
terraform -chdir="$root" plan -input=false -no-color $(vars_for "$role" "$stack") -out="$WORK/plans/$stack.tfplan" >/dev/null
# The state must still be the one the reviewed plan started from, read after this plan was made: a change before or
# during it shows here, and Terraform refuses to apply a saved plan whose state changed after it. Another run that
# applied this stack in between (vending's runs on main do not queue: docs/pipelines.md) would otherwise let an older
# plan's values, whose fingerprints may still match, undo the newer change.
reviewed_version=$(cat "$WORK/reviewed/version-$stack.txt" 2>/dev/null || echo "not recorded")
now_version=$(terraform -chdir="$root" state pull | python3 "$CORE/tools/ci/state_version.py")
if [[ $now_version != "$reviewed_version" ]]; then
  echo "::error::$stack's state changed after this plan was reviewed (it was at $reviewed_version, it is at $now_version): another run applied it. Nothing is applied. Start a new run on main for a fresh plan (gh workflow run mlz.yml --ref main)."
  exit 1
fi
terraform -chdir="$root" show -json "$WORK/plans/$stack.tfplan" > "$WORK/plans/$stack.json"
if ! python3 "$CORE/tools/ci/summary.py" "$WORK/plans/$stack.json" "$stack" "$WORK/apply-summary-$stack.md" "$WORK/apply-changes-$stack.txt"; then
  echo "::error::The plan is refused; nothing is applied."
  cat "$WORK/apply-summary-$stack.md"
  exit 1
fi
if ! diff -u "$reviewed" "$WORK/apply-changes-$stack.txt" > "$WORK/difference-$stack.txt"; then
  # Re-running this run's jobs compares with the same reviewed plan, so after an apply that failed half way it
  # always fails again: only a new run plans afresh.
  echo "::error::The plan differs from the one the reviewer saw; nothing is applied. Start a new run on main for a fresh plan (Actions, mlz, Run workflow; or gh workflow run mlz.yml --ref main): re-running this run's jobs would compare with the same reviewed plan again."
  cat "$WORK/difference-$stack.txt"
  exit 1
fi
echo "The plan matches the reviewed one: $(wc -l < "$reviewed" | tr -d ' ') change(s)."
terraform -chdir="$root" apply -input=false -no-color "$WORK/plans/$stack.tfplan"
