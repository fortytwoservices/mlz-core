# shellcheck shell=bash
# Shared by the pipeline scripts. Run from the customer repo's root, with mlz-core checked out in .mlz-core.
set -euo pipefail

CORE=$PWD/.mlz-core
WORK=$PWD/.mlz                       # everything a run works out; never committed
# Plans live apart from the configs the roots read (.mlz/platform.json is the platform's config).
mkdir -p "$WORK/plans"
export PYTHONPATH=$CORE/tools

# The Terraform root of a role's stack: the platform's firewall rules have a root of their own, and
# management's and identity's networks share one.
root_of() {
  case $1/${2:-} in
    workload/*) echo "$PWD" ;; # a landing zone's workload repo: the team's own root, at the repo's top
    platform/firewall) echo "$CORE/platform/rules/terraform" ;;
    platform/management | platform/identity) echo "$CORE/platform/networks/terraform" ;;
    *) echo "$CORE/$1/terraform" ;;
  esac
}

# The -var flags a role's root takes, for one stack: a landing zone key for vending, the role otherwise.
vars_for() {
  local role=$1 stack=$2
  case $role in
    governance)
      echo "-var rendered_library_path=$WORK/rendered"
      [[ -f _generated/bootstrap.yaml ]] && echo "-var bootstrap_contract_file=$PWD/_generated/bootstrap.yaml"
      ;;
    platform)
      case $stack in
        firewall) echo "-var config_file=$WORK/firewall.json" ;;
        management | identity)
          echo "-var config_file=$WORK/platform.json -var network=$stack"
          # The organization's ID, which GitHub's network settings resource names (GitHub Actions sets it).
          if [[ $stack == management && -n ${GITHUB_REPOSITORY_OWNER_ID:-} ]]; then echo "-var github_business_id=$GITHUB_REPOSITORY_OWNER_ID"; fi
          ;;
        *) echo "-var config_file=$WORK/platform.json" ;;
      esac
      ;;
    vending) echo "-var config_file=$WORK/vending/$stack.json" ;;
    workload) ;; # the team's root reads mlz.auto.tfvars.json, which vending writes, by itself
    *) echo "unknown role $role" >&2; return 1 ;;
  esac
  return 0
}
