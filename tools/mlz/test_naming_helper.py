"""modules/naming, the Terraform naming helper for workload repos, gives the names tools/mlz/naming.py gives: each case is
rendered by both, through a real terraform apply of the module (no provider, no cloud). Run from tools/:
python3 -m unittest discover -s . -t ."""

from __future__ import annotations

import json
import subprocess
import tempfile
import unittest
from pathlib import Path

from mlz import naming

MODULE = Path(__file__).resolve().parents[2] / "modules" / "naming"
FLAT = "{type}-{org}-{app}-{env}-{region}-{nn}"
# (customer.yaml's naming, type, app, environment, region, purpose, nn)
CASES = {
    "network group": ({"org": "acme"}, "resource_group", "pay", "prod", "norwayeast", "net", 1),
    "network": ({"org": "acme"}, "virtual_network", "pay", "prod", "norwayeast", None, 1),
    "storage account": ({"org": "acme"}, "storage_account", "pay", "prod", "norwayeast", "app-web", 1),
    "key vault, its own template": ({"org": "acme", "templates": {"key_vault": "{type}{org}{app}{purpose}{env}{nn}"}}, "key_vault", "pay", "prod", "norwayeast", "logs", 1),
    "purpose first, empty": ({"org": "acme", "template": "{purpose}-{type}-{app}-{nn}"}, "application_gateway", "pay", "dev", "swedencentral", None, 3),
    "purpose last, empty": ({"org": "acme", "template": "az-{type}-{app}-{purpose}"}, "application_gateway", "pay", "test", "westeurope", None, 1),
    "purpose last, given": ({"org": "acme", "template": "az-{type}-{app}-{purpose}"}, "application_gateway", "pay", "test", "westeurope", "web", 1),
    "no purpose token": ({"org": "acme", "template": FLAT}, "network_security_group", "pay", "uat", "norwayeast", "web", 12),
    "an environment and a region of the customer's": ({"org": "acme", "environments": {"hotfix": "hf"}, "regions": {"westeurope": "weu"}}, "public_ip_address", "pay", "hotfix", "westeurope", "afw", 2),
    "a data collection rule": ({"org": "mlz"}, "data_collection_rule", "mgt", "prod", "norwayeast", "changetracking", 1),
    "an empty purpose, as absent": ({"org": "acme"}, "application_gateway", "pay", "prod", "norwayeast", "", 1),
}


def values(settings: naming.Naming, env: str, region: str) -> dict:
    """What vending writes to a workload repo for the helper (naming.helper_values), the app filled in by each case."""
    return {**naming.helper_values(settings, env, region), "app": None}


def apply(cases: dict[str, dict]) -> tuple[subprocess.CompletedProcess, dict | None]:
    """terraform apply of a root calling the module once per case: its run, and the names, when it succeeded."""
    with tempfile.TemporaryDirectory() as tmp:
        return _apply(Path(tmp), cases)


def _apply(folder: Path, cases: dict[str, dict]) -> tuple[subprocess.CompletedProcess, dict | None]:
    (folder / "main.tf").write_text(
        'variable "cases" { type = map(object({ mlz = any, type = string, purpose = string, nn = number })) }\n'
        f'module "name" {{\n  for_each = var.cases\n  source   = "{MODULE}"\n'
        "  mlz      = each.value.mlz\n  type     = each.value.type\n  purpose  = each.value.purpose\n  nn       = each.value.nn\n}\n"
        'output "names" { value = { for key, found in module.name : key => found.name } }\n')
    (folder / "cases.auto.tfvars.json").write_text(json.dumps({"cases": cases}))
    run = lambda *args: subprocess.run(["terraform", f"-chdir={folder}", *args], capture_output=True, text=True)
    if run("init", "-input=false").returncode:
        raise AssertionError("terraform init failed")
    applied = run("apply", "-auto-approve", "-input=false", "-no-color")
    return applied, json.loads(run("output", "-json", "names").stdout) if applied.returncode == 0 else None


class HelperTest(unittest.TestCase):
    def test_the_helper_names_as_mlz_core_does(self):
        cases, expected = {}, {}
        for key, (given, kind, app, env, region, purpose, nn) in CASES.items():
            settings = naming.settings({"naming": given}, "mlz")
            expected[key] = naming.render(settings, kind, app=app, env=env, region=region, purpose=purpose, nn=nn)
            cases[key] = {"mlz": {**values(settings, env, region), "app": app}, "type": kind, "purpose": purpose, "nn": nn}
        result, names = apply(cases)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(names, expected)
        self.assertEqual(names["network group"], "rg-acme-pay-net-pd-nwe-01")
        self.assertEqual(names["storage account"], "stacmepayappwebpdnwe01")
        self.assertEqual(names["purpose first, empty"], "agw-pay-03")

    def test_a_template_python_refuses_the_helper_refuses(self):
        for template, message in (("{purpose}-{purpose}-{type}-{app}", "A template names each token once."),
                                  ("{type}-{app}!", "A template is tokens in braces, dashes and lower-case letters and digits"),
                                  ("fixed-{app}", "A template has {type}"),
                                  ("{type}-{name}", "A template's tokens are {type}"),
                                  ("-{type}-{app}", "It does not start or end with a dash"),
                                  ("{type}--{app}", "It does not start or end with a dash")):
            with self.assertRaises(naming.ConfigError, msg=template):
                naming.settings({"naming": {"org": "acme", "template": template}}, "mlz")
            # As the default template, and as a type's own.
            for mlz in ({"template": template, "templates": {}}, {"template": naming.TEMPLATE, "templates": {"application_gateway": template}}):
                result, _ = apply({"case": {"mlz": {"org": "acme", "app": "pay", "env": "pd", "region": "nwe", **mlz}, "type": "application_gateway", "purpose": None, "nn": 1}})
                self.assertNotEqual(result.returncode, 0, template)
                self.assertIn(message, " ".join(result.stderr.split()), template)

    def test_codes_python_refuses_the_helper_refuses(self):
        good = {"org": "acme", "app": "pay", "env": "pd", "region": "nwe", "template": naming.TEMPLATE, "templates": {}}
        settings = naming.settings({"naming": {"org": "acme"}}, "mlz")
        with self.assertRaisesRegex(naming.ConfigError, "app is ''"):
            naming.render(settings, "application_gateway", app="", env="prod", region="norwayeast")
        for code, wrong in (("app", ""), ("app", "payments"), ("org", "a"), ("org", "ACME"), ("env", ""), ("env", "prod1"), ("region", "n")):
            result, _ = apply({"case": {"mlz": {**good, code: wrong}, "type": "application_gateway", "purpose": None, "nn": 1}})
            self.assertNotEqual(result.returncode, 0, f"{code}={wrong!r}")
            self.assertIn("The codes are lower-case letters and digits", " ".join(result.stderr.split()), f"{code}={wrong!r}")

    def test_a_name_azure_would_not_take_is_refused_not_cut(self):
        settings = naming.settings({"naming": {"org": "acme"}}, "mlz")
        result, _ = apply({"long": {"mlz": {**values(settings, "prod", "norwayeast"), "app": "pay"}, "type": "key_vault", "purpose": "logs", "nn": 1}})
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("key_vault name kv-acme-pay-logs-pd-nwe-01 is 26 characters, 2 more than Azure allows", " ".join(result.stderr.split()))


if __name__ == "__main__":
    unittest.main()
