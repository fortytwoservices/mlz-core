#!/usr/bin/env bash
# check.sh <role>: checks the customer's YAML and works out what the roots read, into .mlz/.
# Prints the stacks to plan and apply as JSON: one per landing zone for vending, the role otherwise.
# shellcheck source=tools/ci/lib.sh
source "$(dirname "$0")/lib.sh"
role=$1
mkdir -p "$WORK"
case $role in
  governance)
    python3 "$CORE/governance/scripts/render_library.py" --customer customer.yaml --out "$WORK/rendered" >&2
    echo '["governance"]'
    ;;
  platform)
    python3 -m mlz platform --repo . --out "$WORK/platform.json" >&2
    if [[ -d firewall ]]; then
      python3 -m mlz firewall --repo . --platform-config "$WORK/platform.json" --out "$WORK/firewall.json" >&2
      echo '["platform","firewall"]'   # in this order: the rules need the hub's policy
    else
      echo '["platform"]'
    fi
    ;;
  vending)
    python3 -m mlz allocate --repo . --check >&2
    python3 -m mlz vending --repo . --out "$WORK/vending" >&2
    python3 -c 'import json, pathlib, sys; print(json.dumps(sorted(p.stem for p in pathlib.Path(sys.argv[1]).glob("*.json"))))' "$WORK/vending"
    ;;
  *) echo "unknown role $role" >&2; exit 1 ;;
esac
