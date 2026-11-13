"""Tests for names from the customer's template and Azure's rules for each type."""

from __future__ import annotations

import re
import unittest

from mlz import naming
from mlz.merge import ConfigError


def acme(**given) -> naming.Naming:
    return naming.settings({"naming": {"org": "acme", **given}}, "mlz")


class RenderTest(unittest.TestCase):
    def test_the_default_template_for_a_landing_zone(self):
        n = acme()
        self.assertEqual(naming.render(n, "resource_group", app="pay", env="prod", region="norwayeast", purpose="net"), "rg-acme-pay-net-pd-nwe-01")
        self.assertEqual(naming.render(n, "virtual_network", app="pay", env="prod", region="norwayeast"), "vnet-acme-pay-pd-nwe-01")
        self.assertEqual(naming.render(n, "network_security_group", app="pay", env="dev", region="norwayeast", purpose="default"), "nsg-acme-pay-default-dv-nwe-01")
        self.assertEqual(naming.render(n, "user_assigned_identity", app="pay", env="prod", region="swedencentral", nn=2), "id-acme-pay-pd-sdc-02")

    def test_types_without_dashes_or_upper_case(self):
        n = acme()
        self.assertEqual(naming.render(n, "storage_account", app="pay", env="prod", region="norwayeast"), "stacmepaypdnwe01")
        self.assertEqual(naming.render(n, "storage_account", app="pay", env="prod", region="norwayeast", purpose="logs"), "stacmepaylogspdnwe01")
        self.assertEqual(naming.render(n, "key_vault", app="pay", env="prod", region="norwayeast"), "kv-acme-pay-pd-nwe-01")

    def test_too_long_is_refused_never_cut(self):
        with self.assertRaisesRegex(ConfigError, r"key_vault name kv-acme-pay-logs-pd-nwe-01 is 26 characters, 2 more than Azure allows; shorten a short name, give the type a template of its own"):
            naming.render(acme(), "key_vault", app="pay", env="prod", region="norwayeast", purpose="logs")

    def test_a_purpose_may_be_a_subnet_s_name(self):
        n = acme()
        self.assertEqual(naming.render(n, "network_security_group", app="pay", env="prod", region="norwayeast", purpose="app-web"), "nsg-acme-pay-app-web-pd-nwe-01")
        self.assertEqual(naming.render(n, "storage_account", app="pay", env="prod", region="norwayeast", purpose="app-web"), "stacmepayappwebpdnwe01")
        with self.assertRaisesRegex(ConfigError, "purpose is '-web'; it must be 1 to 20 lower-case letters and digits, with hyphens inside"):
            naming.render(n, "network_security_group", app="pay", env="prod", region="norwayeast", purpose="-web")
        # Two hyphens in a row, as a subnet's name may have; a key vault's own rule still refuses them.
        self.assertEqual(naming.render(n, "network_security_group", app="pay", env="prod", region="norwayeast", purpose="app--web"), "nsg-acme-pay-app--web-pd-nwe-01")
        with self.assertRaisesRegex(ConfigError, "no two hyphens in a row"):
            naming.render(acme(template="{type}-{app}-{purpose}"), "key_vault", app="pay", env="prod", region="norwayeast", purpose="a--b")

    def test_a_template_of_its_own_for_a_tight_type(self):
        n = acme(templates={"key_vault": "{type}{org}{app}{purpose}{env}{nn}"})
        self.assertEqual(naming.render(n, "key_vault", app="pay", env="prod", region="norwayeast", purpose="logs"), "kvacmepaylogspd01")
        self.assertEqual(naming.render(n, "virtual_network", app="pay", env="prod", region="norwayeast"), "vnet-acme-pay-pd-nwe-01")  # others keep theirs

    def test_an_empty_optional_token_takes_one_dash_with_it(self):
        n = acme(template="{purpose}-{type}-{app}-{nn}")
        self.assertEqual(naming.render(n, "virtual_network", app="pay", env="prod", region="norwayeast"), "vnet-pay-01")
        self.assertEqual(naming.render(n, "virtual_network", app="pay", env="prod", region="norwayeast", purpose="hub"), "hub-vnet-pay-01")
        n = acme(template="az-{type}-{app}-{purpose}")
        self.assertEqual(naming.render(n, "virtual_network", app="pay", env="prod", region="norwayeast"), "az-vnet-pay")

    def test_short_names_and_codes_come_from_the_customer(self):
        n = acme(environments={"hotfix": "hf", "prod": "pr"}, regions={"westeurope": "weu"})
        self.assertEqual(naming.render(n, "virtual_network", app="pay", env="hotfix", region="westeurope"), "vnet-acme-pay-hf-weu-01")
        self.assertEqual(naming.render(n, "virtual_network", app="pay", env="prod", region="northeurope"), "vnet-acme-pay-pr-ne-01")

    def test_what_a_name_cannot_be_made_from(self):
        with self.assertRaisesRegex(ConfigError, "the environment 'staging' has no short name; add it under naming.environments in customer.yaml"):
            naming.render(acme(), "virtual_network", app="pay", env="staging", region="norwayeast")
        with self.assertRaisesRegex(ConfigError, "app is 'payments'; it must be 2 to 5 lower-case letters and digits"):
            naming.render(acme(), "virtual_network", app="payments", env="prod", region="norwayeast")
        with self.assertRaisesRegex(ConfigError, "purpose is 'Net'"):
            naming.render(acme(), "virtual_network", app="pay", env="prod", region="norwayeast", purpose="Net")
        with self.assertRaisesRegex(ConfigError, "the number 100 must be 1 to 99"):
            naming.render(acme(), "virtual_network", app="pay", env="prod", region="norwayeast", nn=100)

    def test_explicit_names_are_checked_against_the_same_rules(self):
        self.assertEqual(naming.check("virtual_network", "vnet-payments-legacy"), "vnet-payments-legacy")
        with self.assertRaisesRegex(ConfigError, "no two hyphens in a row"):
            naming.check("key_vault", "kv-pay--pd")
        with self.assertRaisesRegex(ConfigError, "storage_account name StPay has characters Azure does not allow"):
            naming.check("storage_account", "StPay")
        with self.assertRaisesRegex(ConfigError, "Azure needs at least 3"):
            naming.check("storage_account", "st")
        # A resource group takes Unicode letters and decimal digits, not every character Python counts as one.
        self.assertEqual(naming.check("resource_group", "rg-økonomi-(prod)_1"), "rg-økonomi-(prod)_1")
        with self.assertRaisesRegex(ConfigError, "resource_group name rg-pay. ends with a period"):
            naming.check("resource_group", "rg-pay.")
        for odd in ("rg-½", "rg-x²", "rg x"):
            with self.assertRaisesRegex(ConfigError, "has characters Azure does not allow there"):
                naming.check("resource_group", odd)

    def test_a_type_mlz_core_does_not_name(self):
        for call in (lambda: naming.check("virtual_machine", "vm-01"), lambda: naming.render(acme(), "virtual_machine", app="pay", env="prod", region="norwayeast")):
            with self.assertRaisesRegex(ConfigError, "'virtual_machine' is not a type mlz-core names; use application_gateway, "):
                call()


class SettingsTest(unittest.TestCase):
    def test_the_defaults(self):
        n = naming.settings({}, "mlz")
        self.assertEqual(n.org, "mlz")  # the root ID
        self.assertEqual(naming.render(n, "resource_group", app="hub", env="prod", region="norwayeast"), "rg-mlz-hub-pd-nwe-01")
        self.assertEqual(n.environments["uat"], "ut")
        self.assertEqual(n.regions["westeurope"], "we")  # Microsoft's code

    def problems(self, root_id="mlz", **given) -> str:
        with self.assertRaises(ConfigError) as caught:
            naming.settings({"naming": given}, root_id)
        return str(caught.exception)

    def test_a_naming_section_that_is_not_one_is_refused_not_ignored(self):
        for odd in (False, 0, "", [], "acme"):
            with self.assertRaisesRegex(ConfigError, "customer.yaml: naming is a group of settings"):
                naming.settings({"naming": odd}, "mlz")
        self.assertEqual(naming.settings({"naming": None}, "mlz").org, "mlz")  # naming: with nothing under it

    def test_what_customer_yaml_may_not_say(self):
        self.assertIn("naming.prefix is not a setting", self.problems(prefix="az"))
        self.assertIn("naming.org is 'acmecorp'; it must be 2 to 4", self.problems(org="acmecorp"))
        self.assertIn("the root ID 'contoso-prod' is too long to be the customer's code in names, which is 2 to 4 lower-case letters and digits; set naming.org.", self.problems(root_id="contoso-prod"))
        naming.settings({"naming": {"org": "cso"}}, "contoso-prod")  # fine once naming.org says so
        self.assertIn("{name} is not a token", self.problems(template="{type}-{name}"))
        self.assertIn("starts or ends with a dash, or has two in a row", self.problems(template="{type}--{app}"))
        self.assertIn("has no {type}", self.problems(template="{app}-{env}"))
        self.assertIn("has 'A' at 1", self.problems(template="Az-{type}"))
        self.assertIn("naming.templates.virtual_machine is not a type mlz-core names", self.problems(templates={"virtual_machine": "{type}"}))
        self.assertIn("naming.environments.hotfix is 'hot-fix'", self.problems(environments={"hotfix": "hot-fix"}))
        self.assertIn("naming.regions.atlantis is not an Azure region", self.problems(regions={"atlantis": "atl"}))
        self.assertIn("gives westeurope the code nwe, which norwayeast has too", self.problems(regions={"westeurope": "nwe"}))
        for key, value in (("templates", "oops"), ("environments", ["prod"]), ("regions", "weu")):
            self.assertIn(f"naming.{key} maps each name to its value, not {value!r}", self.problems(**{key: value}))


class RulesTest(unittest.TestCase):
    def test_every_rule_is_usable(self):
        slugs = [rule["slug"] for rule in naming.RULES.values()]
        self.assertEqual(len(slugs), len(set(slugs)))
        for kind, rule in naming.RULES.items():
            if "pattern" in rule:
                re.compile(rule["pattern"])
            self.assertRegex(rule["slug"], r"^[a-z]{2,6}$", kind)
            self.assertIsNotNone(rule["max_length"], kind)


if __name__ == "__main__":
    unittest.main()
