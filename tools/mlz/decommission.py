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
from .vending import IP_PLAN, NAMES, decommissioned, ip_plan, landing_zones, recorded_names, render_names, render_plan

RULES = "firewall/landing-zones"
RULE_COLLECTION_GROUPS = "2025-07-01"  # the API version the platform's rules root writes them with


def rules_in_azure(azure, platform: dict, key: str, cidr: str) -> str | None:
    """Why the landing zone's rules may still be in a hub's firewall policy, or None once they are surely gone
    from every hub: the shared rules, and rules naming it by key, are in every region's policy, not only its
    own. In none is a rule collection group still being changed or failed its change, holding <key>-network or
    <key>-application, or holding a rule with the landing zone's range as a source or destination. Its exact
    range, not any overlap: the platform's own rules come from whole landing zone ranges. A hub's policy is
    named as its network is, afwp- for vnet- (the platform root's naming)."""
    own = {f"{key}-network", f"{key}-application"}
    for region, hub in sorted(platform["regions"].items()):
        prefix, _, network = hub["hub_network"].rpartition("/providers/Microsoft.Network/virtualNetworks/")
        policy = f"{prefix}/providers/Microsoft.Network/firewallPolicies/{network.replace('vnet-', 'afwp-', 1)}"
        page = azure.resource(f"{policy}/ruleCollectionGroups", RULE_COLLECTION_GROUPS) or {}
        groups = page.get("value") or []
        while page.get("nextLink"):  # already carries its api-version and continuation
            page = azure.request("GET", page["nextLink"]) or {}
            groups += page.get("value") or []
        for group in groups:
            properties, name = group.get("properties") or {}, f"{region}: {group.get('name')}"
            state = properties.get("provisioningState")
            if state != "Succeeded":
                return f"rule collection group {name} is {state or 'in an unknown state'}"
            for collection in properties.get("ruleCollections") or []:
                if collection.get("name") in own:
                    return f"{name}/{collection['name']}"
                for rule in collection.get("rules") or []:
                    if cidr in (rule.get("sourceAddresses") or []) + (rule.get("destinationAddresses") or []):
                        return f"{name}/{collection.get('name')}/{rule.get('name')}, from or to {cidr}"
    return None


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


def offer(github, repo: str, branch: str, files: dict[str, str | None], title: str, body: str, at: str | None = None) -> str:
    """The changes as a pull request from the tool's own branch, made from main's head, or from the commit `at`
    they were worked out from; an open one is left as it is."""
    url = github.open_pull(repo, branch)
    if url:
        return url
    head = github.reset_branch(repo, branch, "main", at)
    github.commit_files(repo, branch, head, files, title)
    return github.create_pull(repo, branch, "main", title, body)


def cleanup(github, azure, repo: Path, repositories: dict[str, str], run_url: str, out: Callable[[str], None] = print, sha: str | None = None) -> None:
    """sha: the vending commit the checkout is at. Vending's pull request starts from it, so a main that moved on
    meanwhile shows as a conflict, never as its newer files overwritten."""
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
        still = rules_in_azure(azure, platform, key, str(plan[key]["range"]))
        if still:
            out(f"decommission: {key}'s rules may still be in Azure ({still}): its file and range go once the platform's apply has taken them out")
            continue
        files: dict[str, str | None] = {path: None for path in own_files(repo, zone["where"])}
        if not any(z["workload"] == zone["workload"] for k, z in zones.items() if k != key):
            files.update({path: None for path in own_files(repo, f"landing-zones/{zone['workload']}/_workload")})
        files[IP_PLAN] = render_plan({k: v for k, v in plan.items() if k != key}, retired)
        files[NAMES] = render_names({k: v for k, v in recorded_names(repo).items() if k != key})
        url = offer(github, repositories["vending"], branch, files,
                    f"chore: remove decommissioned {key} and release its range",
                    f"{key} is decommissioned: its network is destroyed and its subscription is in Decommissioned ({run_url}). "
                    f"This deletes its file and releases its range, {plan[key]['range']}, which a new landing zone may then be given. "
                    "The platform's rules file for it is gone already, and its rules are out of Azure.\n", at=sha)
        out(f"decommission: {key}'s file and range go in {url}")
