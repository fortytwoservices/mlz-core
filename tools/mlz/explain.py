"""What a stack ends up with, and where each value comes from.

    python3 -m mlz explain --repo <mlz-vending> <key>                  a landing zone
    python3 -m mlz explain --repo <mlz-platform> <region>              a hub: connectivity/<region>.yaml
    python3 -m mlz explain --repo <mlz-platform> management|identity/<region>|firewall-rules
    python3 -m mlz explain --repo <mlz-governance>                     governance: governance.yaml
    ... --inputs                                                       every input of the stack's module calls

Three parts: every setting the stack's files set, with the file that set it and any value it replaced there; what
mlz-core fills in where nothing is set; and what is worked out. Then the stack's check. A landing zone's worked-out
part is its own (the management group, the tags, the addresses, the hub, DNS servers and routes, the names); every
other stack's comes from its root's input map (tools/mlz/inputs.py): each worked-out input of its module calls, with
its value as the stack's config gives it to the root.

--inputs shows the module calls as the root makes them: every input, its class (setting, derived, fixed, open,
closed), its value where the config or the map holds it, and where it comes from. docs/reference/ has the same
without the values.
"""

from __future__ import annotations

from pathlib import Path

from . import advanced, governance, inputs, naming, platform, schema, vending
from .files import SUFFIXES, read_setting, setting_names
from .merge import ConfigError

ORDER = ("owner", "cost_center", "archetype", "region", "subscription", "billing_scope", "budget", "network", "github", "tags")
REQUIRED = ("owner", "cost_center", "archetype", "subscription", "region")
WIDTH = 44  # a value longer than this puts its source on the next line


def leaves(data: dict, path: tuple = ()) -> dict[tuple, object]:
    """Every value at the end of a path: a group's settings one by one, a list as one value."""
    found = {}
    for key, value in data.items():
        here = (*path, key)
        if isinstance(value, dict) and value:
            found.update(leaves(value, here))
        else:
            found[here] = value
    return found


def file_of(repo: Path, name: str) -> str:
    """A layer's name as the file it is (landing-zones/payments/prod.yaml), or its folder of chunks."""
    for suffix in SUFFIXES:
        if (repo / f"{name}{suffix}").is_file():
            return f"{name}{suffix}"
    return f"{name}/ (chunks)" if (repo / name).is_dir() else name


def show(value) -> str:
    """A value as it is written in YAML."""
    if isinstance(value, list):
        return "[" + ", ".join(show(v) for v in value) + "]"
    if isinstance(value, dict):
        return "{" + ", ".join(f"{k}: {show(v)}" for k, v in value.items()) + "}"
    if isinstance(value, bool):
        return "true" if value else "false"
    return "null" if value is None else str(value)


def explain(repo: Path, key: str, lz: dict, plan: dict, governance: dict | None, platform: dict | None) -> dict[str, list[tuple[str, str, str]]]:
    """The landing zone's values by part, each as (setting, value, where it comes from)."""
    settings, final = lz["settings"], leaves(lz["settings"])
    # Every value each layer set, in layer order: (layer, file, path, value).
    set_by = [(index, file_of(repo, name), path, value) for index, (name, data) in enumerate(lz["layers"]) for path, value in leaves(data).items()]

    def order(path: tuple):
        return (ORDER.index(path[0]) if path[0] in ORDER else len(ORDER), [str(p) for p in path])

    def dotted(path: tuple) -> str:
        return ".".join(map(str, path))

    layered = []
    for path in sorted(final, key=order):
        mine = [entry for entry in set_by if entry[2] == path]
        last, source = (mine[-1][0], mine[-1][1]) if mine else (len(lz["layers"]), file_of(repo, vending.origin(lz, *map(str, path))))
        # What an earlier layer set here, below it (a group a later layer replaced whole) or above it.
        replaced = [
            f"{file}: {show(value)}" if at == path else f"{file}: {dotted(at)} {show(value)}"
            for index, file, at, value in set_by
            if index < last and (at == path and value != final[path] or at[:len(path)] == path and at != path or path[:len(at)] == at and at != path)
        ]
        layered.append((dotted(path), show(final[path]), source + (f", replacing {'; '.join(replaced)}" if replaced else "")))

    # The subscription, unless vending makes it where billing_scope says.
    filled = [(k, "not set", "required: set it in one of the layers") for k in REQUIRED
              if settings.get(k) in (None, "") and not (k == "subscription" and vending.makes_subscription(lz))]
    if vending.makes_subscription(lz):
        alias = f", by the alias {vending.subscription_alias(governance['root'], key)}" if governance is not None else ""
        filled.append(("subscription", "made by vending", f"none is handed over: vending's apply makes it{alias}, billed where billing_scope says ({vending.agreement(settings['billing_scope'])})"))
    budget, network = settings.get("budget"), settings.get("network")
    budget, network = budget if isinstance(budget, dict) else {}, network if isinstance(network, dict) else {}
    if budget.get("monthly") is None:
        filled.append(("budget.monthly", "not set", "no budget"))
    if not budget.get("contacts"):  # none, or an empty list: the alerts go to the owner, as vending.build has it
        filled.append(("budget.contacts", show([settings.get("owner")]), "the owner, as no contacts are given"))
    size = network.get("size", vending.DEFAULT_SIZE)
    if "size" not in network:
        filled.append(("network.size", str(vending.DEFAULT_SIZE), "mlz-core's default"))
    if network.get("subnets") is None:
        default = max(26, size) if isinstance(size, int) else 26
        filled.append(("network.subnets.default", str(default), "mlz-core's default: one subnet, a /26 or the whole of a smaller range"))
    github = settings.get("github") if isinstance(settings.get("github"), dict) else {}
    if vending.creates_repo(lz):
        filled.append(("github.repo", "made by vending", "github.organization is set; governance.yaml's naming.repository names it (below)"))
    elif not github.get("repo"):
        filled.append(("github", "no repo", "github.create is false" if github.get("create") is False else "no github.organization"))

    worked = []
    archetype, region_name = settings.get("archetype"), settings.get("region")
    if governance is None:
        worked.append(("management group", "not known yet", "waits for governance's contract (_generated/governance.yaml)"))
    elif vending.decommissioned(lz):
        worked.append(("management group", f"{governance['root']}-decommissioned", "decommission: true; its plan destroys everything vending made"))
    elif isinstance(archetype, str) and archetype in governance.get("archetypes", {}):
        worked.append(("management group", governance["archetypes"][archetype], f"governance's contract, for archetype {archetype}"))
    tags = {"Owner": (settings.get("owner"), "owner"), "CostCenter": (settings.get("cost_center"), "cost_center"),
            "Environment": (lz["environment"], "the file name"), "Workload": (lz["workload"], "the folder name"),
            "ManagedBy": ("mlz-core", "mlz-core")}
    worked += [(f"tag {name}", show(value), f"from {source}") for name, (value, source) in tags.items()]
    entry = plan.get(key)
    if entry is None:
        worked.append(("address range", "not given yet", "the IP allocator gives it on the pull request"))
    else:
        worked.append(("address range", str(entry["range"]), f"the IP plan ({vending.IP_PLAN})"))
        worked += [(f"subnet {name}", str(block), "the IP plan") for name, block in entry["subnets"].items()]
        worked += [(f"subnet {name}", str(block), "the IP plan: removed, held until released") for name, block in entry["removed_subnets"].items()]
    region = (platform or {}).get("regions", {}).get(region_name) if isinstance(region_name, str) else None
    if platform is None:
        worked.append(("hub", "not known yet", "waits for the platform's contract (_generated/platform.yaml)"))
    elif region:
        source = f"the platform's contract, for {region_name}"
        worked.append(("hub network", region["hub_network"], source))
        worked.append(("DNS servers", show(region["dns_servers"]), source))
        worked += [(f"route {r['name']}", f"{r['to']} via {r['via']}", source) for r in region.get("spoke_routes") or []]
    if governance is not None and entry is not None:
        # Names, as vending gives them: the landing zone's own under names:, the one recorded when first given, or
        # the template's, which the IP allocator records on the pull request.
        everything = vending.recorded_names(repo)
        recorded = vending.flat(everything.get(key, {}))
        names, given = landing_zone_names(key, lz, entry, governance, everything, [])  # problems() reports what stops one
        fresh = vending.flat(landing_zone_names(key, lz, entry, governance, {}, [])[1])  # the template's, nothing kept
        try:
            settings = naming.settings({"naming": governance.get("naming") or None}, governance["root"])
        except ConfigError:
            settings = None  # problems() reports it
        for item, value in vending.flat({k: v for k, v in names.items() if k in vending.NAMED or k in (vending.REPOSITORY, vending.SUBSCRIPTION, "network_security_groups")}).items():
            if not value:
                continue
            label = f"name NSG of {item.split('.', 1)[1]}" if item.startswith("network_security_groups.") else f"name {item}"
            if item not in vending.flat(given):  # the record holds all but what the landing zone names itself
                source = f"names: in {file_of(repo, lz['where'])}"
            elif item in recorded:
                source = f"recorded in {vending.NAMES}"
                # Whether the recorded name is the one the template gives now: a counted NSG keeps the number it was first
                # given, which a fresh count need not repeat, so for it any number will do. Only a name the template no
                # longer gives is a change to report, and only a current one has the template's number to explain.
                current = numbered_alike(value, lz, settings) if item.startswith("network_security_groups.") else fresh.get(item) == value
                if fresh.get(item) not in (None, value) and not (item.startswith("network_security_groups.") and current):
                    source += f"; the template now gives {fresh[item]} (./mlz names --repo . --renew {key})"
                if current:
                    source += number_origin(item, settings)
            else:
                source = "the naming template (docs/naming.md), recorded by the IP allocator on the pull request" + number_origin(item, settings)
            worked.append((label, value, source))
    return {"Set in its layers": layered, "Filled in by mlz-core": filled, "Worked out": worked}


def numbered_alike(value: str, lz: dict, settings: naming.Naming | None) -> bool:
    """Whether a recorded NSG name is the one the template gives now with some number: the number is the only
    difference a fresh count can show for it."""
    if settings is None or not naming.counts(settings, "network_security_group"):
        return False
    app, env, region = vending.short_name(lz), lz["environment"], lz["settings"].get("region")
    if not all(isinstance(part, str) for part in (app, env, region)):
        return False  # a region that is a list, say: problems() says what it must be
    return any(vending.nsg_name(settings, app, env, region, n).lower() == value.lower() for n in range(1, 100))


def number_origin(item: str, settings: naming.Naming | None) -> str:
    """Why a name has its {nn}, where its type's template has no {purpose} and mlz-core numbers its own (docs/naming.md,
    without {purpose}); "" otherwise."""
    if settings is None:
        return ""
    if item.startswith("network_security_groups."):
        if not naming.counts(settings, "network_security_group"):
            return ""
        return "; NSGs are numbered: the lowest number no NSG of the landing zone held when it was first named"
    if item not in vending.NUMBERS or not naming.counts(settings, vending.NAMED[item][0]):
        return ""
    kind = vending.NAMED[item][0]
    alike = [other for other in vending.NUMBERS if vending.NAMED[other][0] == kind]
    plural = {"resource_group": "resource groups", "user_assigned_identity": "identities"}.get(kind, f"{kind.replace('_', ' ')}s")
    return f"; number {vending.NUMBERS[item]:02d} of the landing zone's {plural} ({', '.join(alike)})"


def landing_zone_names(key: str, lz: dict, entry: dict, governance: dict, recorded_names: dict, found: list[str]) -> tuple[dict, dict]:
    """The names the IP allocator would give the landing zone, keeping those recorded, and its record's entry; what
    stops any of them goes in found."""
    try:
        settings = naming.settings({"naming": governance.get("naming") or None}, governance["root"])
    except ConfigError as error:
        found.append(f"_generated/governance.yaml: {error}")
        return {}, {}
    return vending.landing_zone_names(key, lz, sorted(entry["subnets"]), recorded_names.get(key, {}), settings, found)


def problems(key: str, lz: dict, plan: dict, governance: dict | None, platform: dict | None, recorded_names: dict | None = None) -> list[str]:
    """This landing zone's own problems, as the vending check finds them; the check also compares it with
    the others."""
    found: list[str] = []
    if governance is not None and platform is not None:
        vending.check(key, lz, governance, platform, found)
    else:
        # The rest of check() needs the contracts; a required setting set nowhere needs nothing but the layers.
        found += vending.required_problems(lz)
    wanted = vending.wanted_network(lz, found)
    if key in plan and wanted:
        if set(plan[key]["subnets"]) != set(wanted[1]):
            found.append(f"{key}: its subnets in {vending.IP_PLAN} are not the ones asked for; the IP allocator gives them on the pull request.")
        found += vending.size_problems(key, plan[key], wanted)
    if key in plan:
        if platform is not None:
            # What no longer holds for what it was given: overlaps with the hubs and reserved ranges, subnets
            # outside its range, a range outside its region's pool. Its plan alone, so no other's problems show.
            found += vending.plan_problems({key: plan[key]}, {key: lz}, {key: wanted}, platform)
        given = plan[key]["range"]
        found += [f"{vending.IP_PLAN}: {key}'s range {given} overlaps {other}'s {entry['range']}."
                  for other, entry in sorted(plan.items()) if other != key and given.overlaps(entry["range"])]
        if governance is not None:
            landing_zone_names(key, lz, plan[key], governance, recorded_names or {}, found)
    return list(dict.fromkeys(found))


def clean(complete: bool) -> str:
    """What the check says when it finds nothing: all of it, or what it could check before the contracts came."""
    return ("no problems in this landing zone" + ("" if complete else "; the checks that need the contracts wait for them")
            + " (./mlz check checks them all together)")


def render(repo: Path, key: str, lz: dict, parts: dict[str, list[tuple[str, str, str]]], found: list[str], complete: bool = True) -> str:
    return render_parts(f"{key} ({file_of(repo, lz['where'])})", parts, found, complete)


def render_parts(heading: str, parts: dict[str, list[tuple[str, str, str]]], found: list[str] | None, complete: bool = True,
                 nothing: str | None = None) -> str:
    """The parts as aligned rows, then the check (nothing: what it says when it finds nothing); no check at all with
    found None (--inputs)."""
    rows = [row for part in parts.values() for row in part]
    first = max([len(setting) for setting, _, _ in rows] + [10]) + 2
    lines = [heading]
    for title, part in parts.items():
        if not part:
            continue
        lines += ["", title]
        for setting, value, source in part:
            if len(value) > WIDTH:
                lines += [f"  {setting:<{first}}{value}", f"  {'':<{first}}<- {source}"]
            else:
                lines.append(f"  {setting:<{first}}{value:<{WIDTH}}  <- {source}")
    if found is not None:
        lines += ["", "Check"]
        lines += [f"  {problem}" for problem in found] or [f"  {nothing or clean(complete)}"]
    return "\n".join(line.rstrip() for line in lines) + "\n"


# --- Every other stack: its settings, and what its root is given, from the input maps (tools/mlz/inputs.py) ----------

MISSING = object()
LONG = 80  # a module default longer than this is cut: the reference page and the module have it whole
WAITS = "waits for governance's contract (_generated/governance.yaml)"


def role_of(repo: Path) -> str:
    """The repo's role, by what is in it."""
    if (repo / "landing-zones").is_dir() or (repo / vending.DEFAULTS).is_dir():
        return "vending"
    if (repo / platform.CONNECTIVITY).is_dir():
        return "platform"
    if (repo / governance.FILE).is_file():
        return "governance"
    if (refused := governance.renamed(repo)) is not None:
        raise ConfigError(refused)
    raise ConfigError(f"{repo}: neither landing-zones/, {platform.CONNECTIVITY}/ nor governance.yaml; run explain in a vending, platform or governance repo.")


def plain(text: str) -> str:
    return text.replace("`", "")


def default_of(info: dict) -> str:
    text = "required" if info["required"] else info.get("default") or "null"
    return text if len(text) <= LONG else text[:LONG - 3] + "..."


def input_value(item: dict, info: dict, config: dict | None, places: dict[str, str]) -> str | None:
    """What the root gives a module input, where the config or the map says it; None where neither does."""
    kind = item["class"]
    if kind in ("setting", "derived"):
        if config is None or "config" not in item:
            return None
        try:
            return show(inputs.value_at(config, item["config"], places))
        except KeyError:
            return "none: not built here"
    if kind == "fixed":
        return show(item["value"]) if "value" in item else f"the list in {item['from']}" if "from" in item else default_of(info)
    return default_of(info) if kind == "open" else None


def input_source(item: dict, file: str | None) -> str:
    kind = item["class"]
    if kind == "setting":
        return f"setting: {item['key']} in {plain(inputs.FILES[item.get('file', file)])}" + (f", {item['by']}" if item.get("by") else "")
    if kind == "derived":
        names = [item["names"]] if isinstance(item.get("names"), str) else item.get("names") or []
        where = plain(inputs.NAMES_IN.get(file, inputs.FILES.get(file, "")))
        return f"derived: {item['by']}" + (f"; names.{' or names.'.join(names)} in {where} sets it" if names else "")
    if kind == "fixed":
        return f"fixed by Fortytwo: {item['why']}"
    if kind == "open":
        return "open: the module's default; mlz-core does not set it" + (f" ({item['why']})" if item.get("why") else "")
    return f"closed: {item['why']}"


def inactive(root: str, config: dict | None, places: dict[str, str]) -> dict[tuple[str, str], str]:
    """What the root gives nothing for this stack, though its config or the module's defaults hold values: by (module
    call, input path; "" for every input of the call), what explain says instead. The roots' own conditions: a hub's
    Bastion or resolver turned off is null, so nothing in it is given (platform/terraform); a decommissioned landing
    zone has no route table, and sub-vending, which still places its subscription, gets empty maps; without
    budget.monthly the budget map is empty (vending/terraform)."""
    if root == "platform/terraform" and config:
        hub = config["hubs"].get(places.get("region"), {})
        return {("connectivity", f"hub_virtual_networks.{part}"): f"none: not built ({setting} is false)"
                for part, setting in (("bastion", "bastion"), ("dns_resolver", "dns_resolver")) if hub.get(part) is None}
    if root != "vending/terraform" or not config:
        return {}
    gone = "none: decommissioned, its plan destroys what vending made"
    if config.get("decommission"):
        return {("route_table", ""): gone, **{("vending", m): gone for m in ("network_security_groups", "user_managed_identities", "virtual_networks", "budgets", "role_assignments")}}
    return {("vending", "budgets"): "none: no budget (budget.monthly)"} if (config.get("budget") or {}).get("monthly") is None else {}


def module_inputs(root: str, config: dict | None, places: dict[str, str], worked_out: bool = False) -> dict[str, list[tuple[str, str, str]]]:
    """Each module call of a root, with a row per entry of its map: (input, value, source). worked_out: one part, of the
    derived inputs whose value the config holds, named by their call where the root makes several."""
    snapshot, maps = inputs.load_snapshot(), inputs.load_maps()
    calls, parts, off = snapshot[root], {}, inactive(root, config, places)
    for key, call in calls.items():
        section = maps[root][key]
        entries = {path: inputs.entry(value) for path, value in (section.get("inputs") or {}).items()}
        rows, set_here = [], advanced.given(root, key, config, places)  # (input path, as written, value)
        for path, info in call["inputs"].items():
            if path not in entries:
                continue
            item, value = entries[path], input_value(entries[path], info, config, places)
            reason = next((why for (call, prefix), why in off.items() if call == key and (prefix == "" or path == prefix or path.startswith(prefix + "."))), None)
            mine = [(written, v) for at, written, v in set_here if at == path or at.startswith(path + ".")]
            if not worked_out and item["class"] == "open" and mine and not reason:  # advanced: sets it, or part of it
                whole = len(mine) == 1 and mine[0][0] == advanced.written_path(advanced.scope_of(root, key), path)
                rows.append((path, show(mine[0][1] if whole else dict(mine)), f"open: set under {plain(advanced.where(root, key, path))}"))
                continue
            value = reason if reason and value is not None else value
            if worked_out and (item["class"] != "derived" or value is None):
                continue
            label = f"{key}.{path}" if worked_out and len(calls) > 1 else path
            source = input_source(item, section.get("file"))
            rows.append((label, "-" if value is None else value, source.removeprefix("derived: ") if worked_out else source))
        title = "Worked out" if worked_out else f"module.{key.replace('.', '.module.')}: {inputs.module_label(call)}"
        parts.setdefault(title, []).extend(rows)
    return parts


def problems_of(error: ConfigError) -> list[str]:
    """A loader's ConfigError as its problems, one per line, without the heading."""
    lines = [line.strip() for line in str(error).splitlines() if line.strip()]
    return lines[1:] if len(lines) > 1 and lines[0].endswith(":") else lines


def platform_target(repo: Path, target: str | None, governance: dict | None) -> tuple[str, str, dict[str, str]]:
    """A platform target as (stack, root, places): a region or connectivity/<region>, management[/<region>],
    identity/<region>, or firewall-rules."""
    regions = setting_names(repo / platform.CONNECTIVITY)
    name, _, region = (target or "").partition("/")
    if name == "firewall-rules":
        return "firewall-rules", "platform/rules/terraform", {}
    if name in ("management", "identity"):
        primary = (governance or {}).get("primary_region")
        region = region or primary or ""
        if not region:
            raise ConfigError(f"{name}: give its region, {name}/<region>, or let governance's contract say the primary region.")
        # Only a network the platform builds: management's in the primary region, identity's where its file is.
        if name == "management" and primary and region != primary:
            raise ConfigError(f"management/{region}: management's network is built in the primary region, {primary}, only.")
        if name == "identity" and region not in setting_names(repo / "identity"):
            built = setting_names(repo / "identity")
            raise ConfigError(f"identity/{region}: no file there, so identity's network is not built in {region} "
                              f"({'it is built in ' + ', '.join(built) if built else 'it is built nowhere yet'}).")
        return name, "platform/networks/terraform", {"network": name, "region": region}
    region = region if name == platform.CONNECTIVITY else name
    if region not in regions:
        raise ConfigError(f"{target or 'nothing'}: explain takes a region with a file in {platform.CONNECTIVITY}/ ({', '.join(regions) or 'none yet'}), "
                          "management, identity/<region> or firewall-rules.")
    return platform.CONNECTIVITY, "platform/terraform", {"region": region}


def platform_explain(repo: Path, target: str | None, governance: dict | None, vending_contract: dict | None, show_inputs: bool) -> str:
    stack, root, places = platform_target(repo, target, governance)
    config, found = None, []
    if platform.unset(repo):
        found = [f"no file in {platform.CONNECTIVITY}/ has a setting yet: the platform waits, and builds nothing until one does"]
    elif governance is not None:
        try:
            config = platform.build(repo, governance, vending_contract)
        except ConfigError as error:
            if show_inputs and stack != "firewall-rules":  # the values would be missing: say why, and fail
                raise
            found = problems_of(error)
    if show_inputs and stack != "firewall-rules" and config is None:
        raise ConfigError(found[0] if found else "explain --inputs needs governance's contract in _generated/: the config it shows is made from it.")
    where = f"{stack}/{places['region']}" if "region" in places else stack
    heading = f"{stack} ({stack}/)" if stack == "firewall-rules" else f"{where} ({file_of(repo, where)})" if file_of(repo, where) != where else where
    if show_inputs:
        return render_parts(heading, module_inputs(root, config, places), None)
    if stack == "firewall-rules":  # rules are listed one by one in their files; their inputs are what explain has to say
        return render_parts(heading, module_inputs(root, None, places), found, nothing="no problems in the platform's settings")
    layered, filled = [], []
    if stack == platform.CONNECTIVITY:
        settings = read_setting(repo / platform.CONNECTIVITY / places["region"]) or {}
        # The hub's own checks need no contract; the rest waits for it. Once any hub has a setting every hub file must be
        # complete, an empty one too, as the loader has it (platform.unset).
        if governance is None and not platform.unset(repo):
            platform.hub(places["region"], settings, found)
        layered = [(dotted(path), show(value), file_of(repo, where)) for path, value in leaves(settings).items()]
        properties = schema.connectivity()["properties"]
        filled = [(key, show(spec["default"]), "mlz-core's default") for key, spec in properties.items() if "default" in spec and key not in settings]
        filled += [(key, "none", what) for key, what in (("reserved", "no range is reserved"), ("names", "every name from the naming template (docs/naming.md)"))
                   if key not in settings]
    else:
        written = read_setting(repo / stack / places["region"]) or {}
        layered = [(dotted(path), show(value), file_of(repo, where)) for path, value in leaves(written).items()]
        if governance is None:  # the file's own checks need no contract; the rest waits for it
            hubs = dict.fromkeys(setting_names(repo / platform.CONNECTIVITY), {})
            platform.network_regions(repo, stack, hubs, places["region"], found)
        filled = [(stack, "no settings", f"{stack}/<region>.yaml takes none yet; {'its being there builds' if stack == 'identity' else 'management builds'} the network")]
    worked = module_inputs(root, config, places, worked_out=True).get("Worked out", []) if config else \
        [("everything else", "not known yet", WAITS if governance is None else "the platform's settings must check first (below)")]
    checked = "no problems in the platform's settings (./mlz check checks every hub together)" if governance is not None else \
        "no problems in this file; the checks that need governance's contract wait for it"
    return render_parts(heading, {"Set in its file": layered, "Filled in by mlz-core": filled, "Worked out": worked}, found, nothing=checked)


def governance_explain(repo: Path, show_inputs: bool) -> str:
    rules = governance
    defaults_file = inputs.CORE / "governance" / "defaults.yaml"
    try:
        defaults, customer = rules.load_yaml(defaults_file), rules.load_yaml(repo / "governance.yaml")
    except rules.RenderError as error:
        raise ConfigError(str(error)) from None
    merged, config, found = rules.merge(defaults, customer), None, []
    try:
        rules.validate(merged)
        config = rules.normalize(merged)
    except (rules.RenderError, ConfigError) as error:
        if show_inputs:  # the values would be missing: say why, and fail
            raise ConfigError(str(error)) from None
        found = problems_of(ConfigError(str(error)))
    heading = "governance (governance.yaml)"
    if show_inputs:
        return render_parts(heading, module_inputs("governance/terraform", config, {}), None)
    defaults_name = "mlz-core's governance/defaults.yaml"
    mine, theirs = leaves(customer), leaves(defaults)
    layered = []
    for path, value in mine.items():
        if path in theirs and theirs[path] != value:
            source = f"governance.yaml, replacing {defaults_name}: {show(theirs[path])}"
        elif path[0] in defaults and path[0] not in rules.MERGED_KEY_BY_KEY and path not in theirs and path == next(p for p in mine if p[0] == path[0]):
            source = f"governance.yaml, replacing {defaults_name}'s {path[0]} whole"
        else:
            source = "governance.yaml"
        layered.append((dotted(path), show(value), source))
    filled = [(dotted(path), show(value), defaults_name) for path, value in leaves(merged).items() if path not in mine]
    given = customer.get("naming") if isinstance(customer.get("naming"), dict) else {}
    if "template" not in given:
        filled.append(("naming.template", naming.TEMPLATE, "mlz-core's default (docs/naming.md)"))
    if "org" not in given:
        filled.append(("naming.org", show(customer.get("root_id")), "the root ID"))
    worked = module_inputs("governance/terraform", config, {}, worked_out=True).get("Worked out", []) if config else \
        [("everything else", "not known yet", "governance.yaml must check first (below)")]
    return render_parts(heading, {"Set in governance.yaml": layered, "Filled in by mlz-core": filled, "Worked out": worked}, found,
                        nothing="no problems in governance.yaml")


def dotted(path: tuple) -> str:
    return ".".join(map(str, path))
