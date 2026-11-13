"""After a landing zone is decommissioned, its files go, each in a pull request a person merges.

    decommission: true in landing-zones/<workload>/<env>.yaml
      -> the approved apply destroys what vending made and moves the subscription to Decommissioned
      -> vending's contract lists it as held, and the platform stops applying its firewall rules
      -> this: first a pull request in the platform repo deleting firewall/landing-zones/<key>.yaml, then, once
         that is merged and the platform's apply has taken its rules out of Azure, one here deleting the landing
         zone's file and releasing its range in the IP plan

That order matters: released, the range may go to a new landing zone, which must not inherit the old one's
rules, so they must be out of Azure first (deleting the file does not do that, the platform's apply does). And
the landing zone leaves vending's contract altogether, so a rules file still in the platform repo would stop
the platform's check. Run by vending's pipeline (the decommission job in
role.yml) after every run on main in which the landing zones are as main says, with the MLZ App's token. Each
run does what is due and leaves an open pull request as it is.
"""

from __future__ import annotations

from pathlib import Path
from typing import Callable

from .files import read_yaml
from .firewall import partition
from .vending import IP_PLAN, decommissioned, ip_plan, landing_zones, render_plan

RULES = "firewall/landing-zones"
RULE_COLLECTION_GROUPS = "2025-07-01"  # the API version the platform's rules root writes them with


def rules_in_azure(azure, platform: dict, region: str, key: str) -> str | None:
    """The hub's rule collection group that still holds the landing zone's rules, or None once they are gone.
    The hub's firewall policy is named as its network is, afwp- for vnet- (the platform root's naming); the
    landing zone's rules are <key>-network and <key>-application in group lz-<nn>, nn its key's partition."""
    hub = platform["regions"][region]["hub_network"]
    prefix, _, network = hub.rpartition("/providers/Microsoft.Network/virtualNetworks/")
    group = f"{prefix}/providers/Microsoft.Network/firewallPolicies/{network.replace('vnet-', 'afwp-', 1)}/ruleCollectionGroups/lz-{partition(key):02d}"
    found = azure.resource(group, RULE_COLLECTION_GROUPS) or {}
    names = {c.get("name") for c in (found.get("properties") or {}).get("ruleCollections") or []}
    return group if names & {f"{key}-network", f"{key}-application"} else None


def own_files(repo: Path, where: str) -> list[str]:
    """A setting's files in the repo: <where>.yaml or .yml, or the chunks in the folder <where>/."""
    base = repo / where
    found = [p for p in (base.parent / f"{base.name}.yaml", base.parent / f"{base.name}.yml") if p.is_file()]
    if base.is_dir():
        found += sorted(p for p in base.rglob("*") if p.is_file())
    return [str(p.relative_to(repo)) for p in found]


def rules_files(github, repo: str, key: str) -> list[str]:
    """The platform's rules files for the landing zone on its main: one file, or a folder of chunks."""
    found = [path for path in (f"{RULES}/{key}.yaml", f"{RULES}/{key}.yml") if github.file(repo, path, "main")[0] is not None]
    return found + github.folder(repo, f"{RULES}/{key}", "main")


def offer(github, repo: str, branch: str, files: dict[str, str | None], title: str, body: str) -> str:
    """The changes as a pull request from the tool's own branch; an open one is left as it is."""
    url = github.open_pull(repo, branch)
    if url:
        return url
    head = github.reset_branch(repo, branch, "main")
    github.commit_files(repo, branch, head, files, title)
    return github.create_pull(repo, branch, "main", title, body)


def cleanup(github, azure, repo: Path, repositories: dict[str, str], run_url: str, out: Callable[[str], None] = print) -> None:
    zones, plan = landing_zones(repo), ip_plan(repo)
    platform = read_yaml(repo / "_generated" / "platform.yaml")
    retired = sorted(set(plan) - set(zones))
    for key in sorted(k for k, zone in zones.items() if decommissioned(zone)):
        branch = f"mlz/decommission-{key}"
        rules = rules_files(github, repositories["platform"], key)
        if rules:
            url = offer(github, repositories["platform"], branch, {path: None for path in rules},
                        f"chore: delete the firewall rules of decommissioned {key}",
                        f"{key} is decommissioned ({repositories['vending']}): its network is destroyed and its subscription is in "
                        f"Decommissioned ({run_url}). Its rules no longer apply; this deletes their file. Once it is merged, vending "
                        "offers the pull request that deletes the landing zone's own file and releases its range.\n")
            out(f"decommission: {key}'s firewall rules go first, in {url}; its own pull request follows once that is merged")
            continue
        zone = zones[key]
        still = rules_in_azure(azure, platform, zone["settings"]["location"], key)
        if still:
            out(f"decommission: {key}'s rules are still in Azure ({still}): its file and range go once the platform's apply has taken them out")
            continue
        files: dict[str, str | None] = {path: None for path in own_files(repo, zone["where"])}
        if not any(z["workload"] == zone["workload"] for k, z in zones.items() if k != key):
            files.update({path: None for path in own_files(repo, f"landing-zones/{zone['workload']}/_workload")})
        files[IP_PLAN] = render_plan({k: v for k, v in plan.items() if k != key}, retired)
        url = offer(github, repositories["vending"], branch, files,
                    f"chore: remove decommissioned {key} and release its range",
                    f"{key} is decommissioned: its network is destroyed and its subscription is in Decommissioned ({run_url}). "
                    f"This deletes its file and releases its range, {plan[key]['range']}, which a new landing zone may then be given. "
                    "The platform's rules file for it is gone already.\n")
        out(f"decommission: {key}'s file and range go in {url}")
