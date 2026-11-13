"""The contracts the roles hand each other, as the YAML files in `_generated/`.

Each root's `contract` output already has the published shape; this adds the header and a comment on
each section, so a person reading the file in their repo knows what it is and where it comes from.
"""

from __future__ import annotations

from .yamlout import dump

CONTRACTS = {
    "vending": {
        "header": (
            "Each landing zone's region and address range, for the platform's firewall rules.\n"
            "Written by CI from mlz-vending after every apply: do not edit here."
        ),
        "comments": {
            "landing_zones": "A firewall rule names a landing zone by its key; this is where its traffic comes from.",
            "held": "Landing zones removed but not yet released: their networks may still exist, so nothing is built over them.",
        },
    },
    "bootstrap": {
        "header": (
            "What the bootstrap set up, for the governance repo.\n"
            "Written by Fortytwo after running the bootstrap: do not edit here."
        ),
        "comments": {
            "state": "Where every role's Terraform state is kept.",
            "identities": (
                "The pipeline identities. Each role's plan identity reads, for pull requests; its apply\n"
                "identity changes Azure, in its approved environment. Governance grants vending's two\n"
                "their rights on Landing Zones and Sandboxes."
            ),
        },
    },
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
            "What the platform built, for the vending repo.\n"
            "Written by CI from mlz-platform after every apply: do not edit here."
        ),
        "comments": {
            "regions": (
                "Per region: the hub landing zones peer with, the platform's range (the hub, management and\n"
                "identity), the landing zone ranges, the ranges reserved for networks mlz-core does not\n"
                "manage, and how their traffic leaves. No landing zone is given an address in the platform's\n"
                "range or a reserved one."
            ),
            "private_dns": "Where the Private Link DNS zones are, for governance's DNS policy.",
        },
    },
}


def render(kind: str, contract: dict) -> str:
    spec = CONTRACTS[kind]
    return dump(contract, spec["header"], spec["comments"])
