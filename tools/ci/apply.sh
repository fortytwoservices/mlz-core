#!/usr/bin/env bash
# apply.sh <role> <stack>: plans and applies against the stack's state in the account, as the apply
# identity, in the role's environment.
# shellcheck source=tools/ci/lib.sh
source "$(dirname "$0")/lib.sh"
role=$1 stack=$2
root=$(root_of "$role")
terraform -chdir="$root" init -reconfigure -input=false -no-color \
  -backend-config="storage_account_name=$TFSTATE_STORAGE_ACCOUNT" \
  -backend-config="container_name=$TFSTATE_CONTAINER" \
  -backend-config="key=$stack.tfstate"
# shellcheck disable=SC2046
terraform -chdir="$root" plan -input=false -no-color $(vars_for "$role" "$stack") -out="$WORK/$stack.tfplan"
terraform -chdir="$root" apply -input=false -no-color "$WORK/$stack.tfplan"
