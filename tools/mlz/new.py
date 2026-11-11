"""Starts a new landing zone's files, from what the layers above it already set.

    python3 -m mlz new landing-zone --repo <mlz-vending> <workload> <env> [--subscription <id>]
        [--owner <email>] [--cost-center <id>] [--archetype corp|online|sandbox] [--location <region>]

It writes landing-zones/<workload>/<env>.yaml and, for a new workload, <workload>/_workload.yaml, with
only what no layer above sets: a required setting given on the command line is written as given, one
not given is written commented out, so the check names it until someone fills it in. A setting given that
a layer above already has at the same value is left out, but for the subscription, which is always the
landing zone's own. It writes nothing over a file that is there, and
commits nothing: the files go into a pull request, whose run gives the landing zone its addresses.
"""

from __future__ import annotations

from pathlib import Path

from . import vending
from .files import read_setting
from .merge import ConfigError, layer
from .yamlout import scalar

# Usually the same in all of a workload's environments, so a new workload's _workload.yaml holds them.
WORKLOAD_SETTINGS = ("owner", "cost_center", "archetype")
# What each required setting is, as the files say it.
COMMENTS = {
    "owner": "the owning team's email address; becomes the Owner tag",
    "cost_center": "becomes the CostCenter tag",
    "archetype": "corp, online or sandbox",
    "location": "a region with a hub",
    "subscription": "handed over to vending before this file is merged",
}
PLACEHOLDERS = {"owner": "team@example.com", "cost_center": "\"0000\"", "archetype": "corp", "location": "norwayeast",
                "subscription": "00000000-0000-0000-0000-000000000000"}


def lines(settings: list[tuple[str, object]]) -> list[str]:
    """One line per setting, its comment aligned; a setting without a value is commented out."""
    shown = [(key, PLACEHOLDERS[key] if value is None else scalar(str(value)), value is None) for key, value in settings]
    width = max((len(f"{key}: {text}") + (2 if missing else 0) for key, text, missing in shown), default=0) + 3
    out = []
    for key, text, missing in shown:
        line = f"{'# ' if missing else ''}{key}: {text}"
        out.append(f"{line:<{width}}# {'required: ' if missing else ''}{COMMENTS[key]}")
    return out


def landing_zone(repo: Path, workload: str, env: str, given: dict[str, str | None]) -> list[Path]:
    """Writes the new landing zone's files; returns them. `given` maps a required setting to its value, or None."""
    root = repo / "landing-zones"
    if not root.is_dir():
        raise ConfigError(f"{root}: no landing-zones folder; is {repo} the vending repo?")
    for name, kind in ((workload, "workload"), (env, "environment")):
        if not vending.NAME.fullmatch(name):
            raise ConfigError(f"{kind} {name!r}: up to 24 lower-case letters, digits and hyphens (it is used in names).")
    key = f"{workload}-{env}"
    zones = vending.landing_zones(repo)
    if key in zones:
        raise ConfigError(f"{zones[key]['where']} is the landing zone {key} already.")
    folder = root / workload
    target = folder / f"{env}.yaml"
    if read_setting(folder / env) is not None:
        raise ConfigError(f"{target} is there already.")

    # What the layers above set, merged as the loader merges them.
    above: dict = {}
    for base in (root / "_defaults" / "all", root / "_defaults" / env, folder / "_workload"):
        above = layer(above, read_setting(base) or {})

    def same(k: str) -> bool:
        # The loader makes one tag of cost_center: 4711 and "4711" (but not of 42 and "0042").
        value = above.get(k)
        return value == given[k] or isinstance(value, int) and not isinstance(value, bool) and str(value) == given[k]

    def needed(keys) -> list[tuple[str, object]]:
        # Given at a value no layer above has, or required and set nowhere above.
        return [(k, given.get(k)) for k in keys if given.get(k) is not None and not same(k) or given.get(k) is None and above.get(k) in (None, "")]

    def create(path: Path, text: str) -> None:
        # Exclusive: a file another run wrote since the checks above is never written over.
        try:
            with path.open("x") as file:
                file.write(text)
        except FileExistsError:
            raise ConfigError(f"{path} is there already.") from None
        written.append(path)

    written: list[Path] = []
    if not folder.exists():
        folder.mkdir()
        body = needed(WORKLOAD_SETTINGS)
        if body:
            create(folder / "_workload.yaml", "\n".join([f"# The {workload} workload, all its environments.", "", *lines(body)]) + "\n")
        rest = ("location",)
    else:
        rest = (*WORKLOAD_SETTINGS, "location")
    # The subscription is always the landing zone's own, whatever a layer above says: written as given, or to fill in.
    body = needed(rest) + [("subscription", given.get("subscription"))]
    header = f"# {workload} in {env}, the landing zone {key}: one subscription, one network, one state."
    extra = [
        "",
        "# budget:",
        "#   monthly: 500                     # a whole amount a month; alerts go to the owner",
        "# network:",
        "#   size: 24                         # the range's prefix length; the IP allocator gives out the block",
        "#   subnets:",
        "#     app: 26",
    ]
    create(target, "\n".join([header, "", *lines(body), *extra]) + "\n")
    return written
