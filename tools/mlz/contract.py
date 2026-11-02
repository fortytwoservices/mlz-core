"""The contracts the roles hand each other, as the YAML files in `_generated/`.

Each root's `contract` output already has the published shape; this adds the header and a comment on
each section, so a person reading the file in their repo knows what it is and where it comes from.
"""

from __future__ import annotations

from .yamlout import dump

CONTRACTS = {
    "governance": {
        "header": (
            "What governance decided, for the platform and vending repos.\n"
            "Written by CI from mlz-governance after every apply: do not edit here."
        ),
        "comments": {
            "allowed_regions": "Landing zones may only use these regions.",
            "required_tags": "Every landing zone resource group must carry these tags.",
            "archetypes": "Where each kind of landing zone is placed.",
            "platform_subscriptions": "The platform's own subscriptions; no landing zone may use them.",
            "monitoring": "Where logs go.",
        },
    },
    "platform": {
        "header": (
            "What the platform built, for the governance and vending repos.\n"
            "Written by CI from mlz-platform after every apply: do not edit here."
        ),
        "comments": {
            "regions": "Per region: the hub landing zones peer with, its ranges, and how their traffic leaves.",
            "private_dns": "Where the Private Link DNS zones are, for governance's DNS policy.",
        },
    },
}


def render(kind: str, contract: dict) -> str:
    spec = CONTRACTS[kind]
    return dump(contract, spec["header"], spec["comments"])
