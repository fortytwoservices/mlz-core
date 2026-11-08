# shellcheck shell=bash
# Shared by the pipeline scripts. Run from the customer repo's root, with mlz-core checked out in .mlz-core.
set -euo pipefail

CORE=$PWD/.mlz-core
WORK=$PWD/.mlz                       # everything a run works out; never committed
# Plans live apart from the configs the roots read (.mlz/platform.json is the platform's config).
mkdir -p "$WORK/plans"
export PYTHONPATH=$CORE/tools

root_of() { echo "$CORE/$1/terraform"; }

# The -var flags a role's root takes, for one stack: a landing zone key for vending, the role otherwise.
vars_for() {
  local role=$1 stack=$2
  case $role in
    governance)
      echo "-var rendered_library_path=$WORK/rendered"
      [[ -f _generated/platform.yaml ]] && echo "-var platform_contract_file=$PWD/_generated/platform.yaml"
      [[ -f _generated/bootstrap.yaml ]] && echo "-var bootstrap_contract_file=$PWD/_generated/bootstrap.yaml"
      ;;
    platform) echo "-var config_file=$WORK/platform.json" ;;
    vending) echo "-var config_file=$WORK/vending/$stack.json" ;;
    *) echo "unknown role $role" >&2; return 1 ;;
  esac
  return 0
}
