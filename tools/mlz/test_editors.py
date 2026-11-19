"""Tests that VS Code and JetBrains give each file of a starter the same schema (decision D11): .vscode/settings.json
and .idea/jsonSchemas.xml cannot carry the same masks, since the two editors read them otherwise, so the test holds
the files together. Each file's path goes through both editors' matching as their sources do it:

- VS Code's YAML extension (Red Hat's yaml-language-server) puts **/ before each glob and matches the whole path:
  **/ spans any number of folders, none included, and * and ? stay within one name.
- JetBrains (UserDefinedJsonSchemaConfiguration.recalculatePatterns, PatternUtil.convertToRegex) makes a mask with a /
  into the regex .*/ + mask, matched against the whole path, and matches a mask without one against the file name;
  its * is .* and crosses folders.

Run from tools/: python3 -m unittest discover -s . -t ."""

from __future__ import annotations

import json
import re
import unittest
import xml.etree.ElementTree as ElementTree
from pathlib import Path

from mlz import install, schema

STARTERS = schema.CORE / "starter"
ROOT = "/home/alex/projects"  # where someone has the repo; the editors see the whole path

# Each role's files, the starter's own and some it does not have, with the schema both editors must give them.
EXPECTED = {
    "governance": {
        "customer.yaml": "customer",
        "bootstrap.yaml": "bootstrap",
        "customer.yml": None,
        "README.md": None,
        ".github/workflows/mlz.yml": None,
    },
    "platform": {
        "connectivity/norwayeast.yaml": "connectivity",
        "connectivity/swedencentral.yml": "connectivity",
        "connectivity/norwayeast/hub.yaml": "connectivity",      # a region's settings in parts
        "management/norwayeast.yaml": "network",
        "identity/norwayeast.yml": "network",
        "firewall-rules/platform.yaml": "firewall-rules",
        "firewall-rules/platform/web.yaml": "firewall-rules",
        "connectivity.yaml": None,
        "connectivity/README.md": None,
        "connectivity-old/norwayeast.yaml": None,
        "_generated/contract.json": None,
        ".github/workflows/mlz.yml": None,
    },
    "vending": {
        "defaults/landing-zone-defaults.yaml": "landing-zone",
        "defaults/prod-defaults.yml": "landing-zone",
        "landing-zones/payments/workload-defaults.yaml": "landing-zone",
        "landing-zones/payments/prod.yaml": "landing-zone",
        "examples/payments/prod.yaml": "landing-zone",
        "_generated/ip-plan.yaml": None,
        "_generated/names.yaml": None,
        "landing-zones/README.md": None,
        ".github/workflows/mlz.yml": None,
    },
}


def vscode(role: str) -> dict[str, list[str]]:
    """Each schema's URL and its globs, from the starter's .vscode/settings.json (JSON with comments)."""
    text = (STARTERS / f"mlz-{role}" / ".vscode" / "settings.json").read_text()
    found = {url: json.loads(value) for url, value in re.findall(r'"(https://[^"]+)":\s*(\[[^\]]*\]|"[^"]*")', text)}
    return {url: [value] if isinstance(value, str) else value for url, value in found.items()}


def jetbrains(role: str) -> dict[str, list[str]]:
    """Each schema's URL and its masks, from the starter's .idea/jsonSchemas.xml."""
    root = ElementTree.parse(STARTERS / f"mlz-{role}" / ".idea" / "jsonSchemas.xml").getroot()
    found = {}
    for info in root.iter("SchemaInfo"):
        options = {option.get("name"): option for option in info.findall("option")}
        items = [{option.get("name"): option.get("value") for option in item.findall("option")} for item in options["patterns"].iter("Item")]
        assert all(item["mappingKind"] == "Pattern" for item in items), items
        found[options["relativePathToSchema"].get("value")] = [item["path"] for item in items]
    return found


def vscode_takes(glob: str, path: str) -> bool:
    regex, rest = "", "**/" + glob.removeprefix("/")
    while rest:
        if rest.startswith("**/"):
            regex, rest = regex + "(?:[^/]*/)*", rest[3:]
        elif rest[0] in "*?":
            regex, rest = regex + ("[^/]*" if rest[0] == "*" else "[^/]"), rest[1:]
        else:
            regex, rest = regex + re.escape(rest[0]), rest[1:]
    return re.fullmatch(regex, path) is not None


def jetbrains_takes(mask: str, path: str) -> bool:
    regex = mask.replace(".", r"\.").replace("*", ".*").replace("?", ".")
    for char in "+()[]/^${}|":
        regex = regex.replace(char, "\\" + char)
    if "/" in mask:
        return re.fullmatch(".*/" + regex, path) is not None
    return re.fullmatch(regex, path.rsplit("/", 1)[1]) is not None


def schemas(mappings: dict[str, list[str]], takes, path: str) -> set[str]:
    return {url.rsplit("/", 1)[1].removesuffix(".schema.json") for url, masks in mappings.items() if any(takes(mask, path) for mask in masks)}


class EditorsTest(unittest.TestCase):
    def test_both_editors_give_each_file_the_same_schema(self):
        for role, expected in EXPECTED.items():
            # The starter's own YAML files too, each of which has one schema (but the workflow, listed above).
            own = {str(p.relative_to(STARTERS / f"mlz-{role}")) for p in (STARTERS / f"mlz-{role}").rglob("*") if p.suffix in (".yaml", ".yml")}
            for path in sorted(own | set(expected)):
                where = f"{ROOT}/mlz-{role}/{path}"
                with self.subTest(role=role, path=path):
                    given = schemas(vscode(role), vscode_takes, where)
                    self.assertEqual(schemas(jetbrains(role), jetbrains_takes, where), given)
                    if path in expected:
                        self.assertEqual(given, {expected[path]} if expected[path] else set())
                    else:
                        self.assertEqual(len(given), 1, given)

    def test_both_editors_read_the_schemas_of_this_release(self):
        release = install.release()
        for role in EXPECTED:
            with self.subTest(role=role):
                self.assertEqual(set(jetbrains(role)), set(vscode(role)))
                for url in jetbrains(role):
                    self.assertTrue(url.startswith(f"https://raw.githubusercontent.com/fortytwoservices/mlz-core/{release}/schemas/"), url)

    def test_the_jetbrains_file_is_the_one_its_ide_reads(self):
        for role in EXPECTED:
            root = ElementTree.parse(STARTERS / f"mlz-{role}" / ".idea" / "jsonSchemas.xml").getroot()
            self.assertEqual([c.get("name") for c in root.iter("component")], ["JsonSchemaMappingsProjectConfiguration"], role)
            for info in root.iter("SchemaInfo"):
                self.assertEqual(info.find("option[@name='schemaVersion']").get("value"), "JSON Schema 7", role)

    def test_the_matching_is_each_editor_s(self):
        # Where the two differ: JetBrains' * crosses folders and its mask without a / takes the name anywhere, which
        # is why its file says management/*.yaml where VS Code's says management/**/*.yaml.
        self.assertTrue(jetbrains_takes("management/*.yaml", f"{ROOT}/mlz-platform/management/a/b.yaml"))
        self.assertFalse(vscode_takes("management/*.yaml", f"{ROOT}/mlz-platform/management/a/b.yaml"))
        self.assertTrue(vscode_takes("management/**/*.yaml", f"{ROOT}/mlz-platform/management/b.yaml"))
        self.assertFalse(jetbrains_takes("management/**/*.yaml", f"{ROOT}/mlz-platform/management/b.yaml"))
        self.assertTrue(jetbrains_takes("customer.yaml", f"{ROOT}/mlz-governance/customer.yaml"))
        self.assertFalse(jetbrains_takes("customer.yaml", f"{ROOT}/mlz-governance/old-customer.yaml"))


if __name__ == "__main__":
    unittest.main()
