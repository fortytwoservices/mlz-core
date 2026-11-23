"""Tests for the platform's, governance's and the bootstrap's names. Run from tools/: python3 -m unittest discover -s . -t ."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from mlz import names, naming
from mlz.merge import ConfigError
from mlz.platform import build
from mlz.test_platform import GOVERNANCE, HUB

FLAT = "{type}-{org}-{app}-{env}-{region}-{nn}"  # a template without {purpose}: mlz-core numbers its own
BARE = "{type}-{org}-{app}-{env}-{region}"  # without {purpose} and {nn}: nothing tells two apart


def settings(**given) -> naming.Naming:
    return naming.settings({"naming": {"org": "acme", **given}}, "mlz")


class ApartTest(unittest.TestCase):
    """Two of a type in one place need names apart, which a template without {app} or {purpose} would not give."""

    def test_governance_checks_only_the_groups_it_knows(self):
        # Without {app}, governance's group and the bootstrap's default would meet; bootstrap.yaml names the bootstrap's
        # elsewhere, which governance never reads, so governance takes it, and the bootstrap's own check decides.
        no_app = settings(templates={"resource_group": "{type}-{org}-{purpose}-{env}-{region}-{nn}"})
        names.governance(no_app, "norwayeast")
        names.bootstrap(no_app, "norwayeast", {"resource_group": "rg-bootstrap-legacy"})
        with self.assertRaisesRegex(ConfigError, "governance's management group and the bootstrap's group would both be rg-acme-pd-nwe-01"):
            names.bootstrap(no_app, "norwayeast")

    def test_governance_s_and_the_bootstrap_s_groups_in_the_management_subscription(self):
        # Without {purpose}, a part's own group is 01 and its network's 02.
        self.assertEqual(names.governance(settings(template=FLAT), "norwayeast")["management"]["resource_group"], "rg-acme-mgt-pd-nwe-01")
        self.assertEqual(names.network_resource_group(settings(template=FLAT), "management", "norwayeast"), "rg-acme-mgt-pd-nwe-02")
        self.assertEqual(names.network_resource_group(settings(template=FLAT), "identity", "norwayeast"), "rg-acme-idn-pd-nwe-02")
        # Without {purpose} and {nn}: refused, saying what to add.
        for call in (lambda: names.governance(settings(template=BARE), "norwayeast"), lambda: names.bootstrap(settings(template=BARE), "norwayeast")):
            with self.assertRaisesRegex(ConfigError, "governance's management group and management's network group would both be rg-acme-mgt-pd-nwe, "
                                                     r"and Azure needs resource groups in the management subscription named apart; put \{app\} and "
                                                     r"\{purpose\} or \{nn\} in the template"):
                call()
        # The bootstrap's own group, named explicitly onto governance's: refused too.
        with self.assertRaisesRegex(ConfigError, "governance's management group and the bootstrap's group would both be RG-acme-mgt-pd-nwe-01"):
            names.bootstrap(settings(), "norwayeast", {"resource_group": "RG-acme-mgt-pd-nwe-01"})

    def test_data_collection_rules(self):
        rules = names.governance(settings(templates={"data_collection_rule": FLAT}), "norwayeast")["management"]["data_collection_rules"]
        self.assertEqual(rules, {"change_tracking": "dcr-acme-mgt-pd-nwe-01", "vm_insights": "dcr-acme-mgt-pd-nwe-02", "defender_sql": "dcr-acme-mgt-pd-nwe-03"})
        with self.assertRaisesRegex(ConfigError, "change_tracking and vm_insights would both be dcr-acme-mgt-pd-nwe"):
            names.governance(settings(templates={"data_collection_rule": BARE}), "norwayeast")

    def test_network_security_groups_need_their_purpose(self):
        # One per subnet, with no record to hold a number when a subnet is added or removed: refused, with the fix.
        with self.assertRaisesRegex(ConfigError, "management's network security groups are told apart by their subnets' names, which their template "
                                                 r"has no \{purpose\} for: give them a template with \{purpose\}, as naming.templates.network_security_group"):
            names.network(settings(templates={"network_security_group": FLAT}), "management", "norwayeast", ["github-runners", "runners"])
        with self.assertRaisesRegex(ConfigError, "github-runners and runners would both be nsg-acme-mgt-pd-nwe, and Azure needs management's network security groups"):
            names.network(settings(templates={"network_security_group": BARE}), "management", "norwayeast", ["github-runners", "runners"])
        # A template without {purpose} for everything else is fine once the NSGs have one of their own.
        named = names.network(settings(template=FLAT, templates={"network_security_group": naming.TEMPLATE}), "management", "norwayeast", ["runners"])
        self.assertEqual((named["virtual_network"], named["network_security_groups"]), ("vnet-acme-mgt-pd-nwe-01", {"runners": "nsg-acme-mgt-runners-pd-nwe-01"}))

    def test_the_pipeline_identities(self):
        identities = names.bootstrap(settings(templates={"user_assigned_identity": FLAT}), "norwayeast")
        self.assertEqual(identities["identities"], {f"{role}-{kind}": f"id-acme-mlz-pd-nwe-0{n}" for n, (role, kind) in enumerate(
            [(r, k) for r in ("governance", "platform", "vending") for k in ("plan", "apply")], 1)})
        self.assertEqual(identities["runners_identity"], "id-acme-mlz-pd-nwe-07")
        with self.assertRaisesRegex(ConfigError, "governance-plan and governance-apply would both be id-acme-mlz-pd-nwe, and Azure needs the pipeline identities"):
            names.bootstrap(settings(templates={"user_assigned_identity": BARE}), "norwayeast")

    def test_the_runners_identity_and_the_vault_s_endpoint_beside_the_bootstrap_s_own(self):
        flat = names.bootstrap(settings(templates={"private_endpoint": FLAT, "network_interface": FLAT}), "norwayeast")
        self.assertEqual([flat[key] for key in ("state_endpoint", "vault_endpoint", "state_endpoint_nic", "vault_endpoint_nic")],
                         ["pep-acme-mlz-pd-nwe-01", "pep-acme-mlz-pd-nwe-02", "nic-acme-mlz-pd-nwe-01", "nic-acme-mlz-pd-nwe-02"])
        with self.assertRaisesRegex(ConfigError, "the state account's endpoint and the vault's endpoint would both be pep-acme-mlz-pd-nwe"):
            names.bootstrap(settings(templates={"private_endpoint": BARE}), "norwayeast")
        named = names.bootstrap(settings(), "norwayeast", {"key_vault": "kv-acme-runners"})
        self.assertEqual({key: named[key] for key in ("key_vault", "vault_endpoint", "vault_endpoint_nic", "runners", "runners_identity", "runners_nsg", "runners_subnet")}, {
            "key_vault": "kv-acme-runners", "vault_endpoint": "pep-acme-mlz-vault-pd-nwe-01", "vault_endpoint_nic": "nic-acme-mlz-vault-pd-nwe-01",
            "runners": "vmss-acme-mlz-pd-nwe-01", "runners_identity": "id-acme-mlz-runners-pd-nwe-01", "runners_nsg": "nsg-acme-mlz-runners-pd-nwe-01",
            "runners_subnet": "snet-runners"})

    def test_the_numbers_never_change(self):
        # A number is in a name: the table is only ever added to (docs/naming.md, without {purpose}).
        self.assertEqual(names.NUMBERS, {
            "the hub's public IPs": {"firewall_public_ip": 1, "firewall_management_public_ip": 2, "bastion_public_ip": 3},
            "a part's resource groups": {"own": 1, "network": 2},
            "data collection rules": {"change_tracking": 1, "vm_insights": 2, "defender_sql": 3},
            "the bootstrap's identities": {"governance-plan": 1, "governance-apply": 2, "platform-plan": 3, "platform-apply": 4,
                                           "vending-plan": 5, "vending-apply": 6, "runners": 7},
            "the bootstrap's endpoints": {"state": 1, "vault": 2},
        })
        self.assertEqual(set(names.NUMBERS["data collection rules"]), set(names.DATA_COLLECTION_RULES))
        self.assertEqual(set(names.NUMBERS["the bootstrap's identities"]), {f"{r}-{k}" for r in names.ROLES for k in ("plan", "apply")} | {"runners"})

    def test_with_purpose_every_number_is_01_as_before(self):
        default = names.bootstrap(settings(), "norwayeast")
        self.assertEqual(default["identities"]["vending-apply"], "id-acme-mlz-vendapply-pd-nwe-01")
        self.assertEqual(default["vault_endpoint"], "pep-acme-mlz-vault-pd-nwe-01")
        self.assertEqual(names.network_resource_group(settings(), "management", "norwayeast"), "rg-acme-mgt-net-pd-nwe-01")

    def test_github_hosted_runners_have_no_vault_or_scale_set_to_name(self):
        # A template whose endpoints carry no {purpose} is fine without the vault's endpoint beside the state account's.
        named = names.bootstrap(settings(templates={"private_endpoint": FLAT}), "norwayeast", self_hosted=False)
        self.assertEqual(named["state_endpoint"], "pep-acme-mlz-pd-nwe-01")
        self.assertEqual([named[key] for key in ("key_vault", "vault_endpoint", "vault_endpoint_nic", "runners", "runners_identity", "runners_nsg")], [None] * 6)

    def test_a_vault_named_in_bootstrap_yaml_is_checked_as_azure_would(self):
        with self.assertRaisesRegex(ConfigError, "names.key_vault: key_vault name kv_runners has characters Azure does not allow"):
            names.bootstrap_explicit({"key_vault": "kv_runners"})

    def test_the_defaults_are_apart(self):
        names.governance(settings(), "norwayeast")
        names.bootstrap(settings(), "norwayeast")
        names.network(settings(), "management", "norwayeast", ["github-runners", "runners", "private-endpoints"])


class HubApartTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.repo = Path(self.tmp.name)
        (self.repo / "connectivity").mkdir()

    def tearDown(self):
        self.tmp.cleanup()

    def problems(self, governance: dict) -> str:
        with self.assertRaises(ConfigError) as caught:
            build(self.repo, governance)
        return str(caught.exception)

    def test_the_public_ips_a_hub_has(self):
        flat_ips = {**GOVERNANCE, "naming": {"templates": {"public_ip_address": FLAT}}}
        bare_ips = {**GOVERNANCE, "naming": {"templates": {"public_ip_address": BARE}}}
        (self.repo / "connectivity" / "norwayeast.yaml").write_text(HUB)
        build(self.repo, bare_ips)  # the firewall's alone: nothing to tell apart
        # Numbered without {purpose}, each by its fixed number: Bastion's is 03 whether or not the firewall has a
        # management IP, so switching one on or off renames nothing.
        (self.repo / "connectivity" / "norwayeast.yaml").write_text(HUB + "bastion: true\n")
        hub = build(self.repo, flat_ips)["hubs"]["norwayeast"]["names"]
        self.assertEqual((hub["firewall_public_ip"], hub["bastion_public_ip"]), ("pip-demo-hub-pd-nwe-01", "pip-demo-hub-pd-nwe-03"))
        self.assertIn("firewall_public_ip and bastion_public_ip would both be pip-demo-hub-pd-nwe, and Azure needs connectivity/norwayeast's public IP addresses",
                      self.problems(bare_ips))
        (self.repo / "connectivity" / "norwayeast.yaml").write_text(HUB + "firewall: Basic\n")
        self.assertEqual(build(self.repo, flat_ips)["hubs"]["norwayeast"]["names"]["firewall_management_public_ip"], "pip-demo-hub-pd-nwe-02")
        self.assertIn("firewall_public_ip and firewall_management_public_ip would both be", self.problems(bare_ips))
        # Named explicitly onto another: refused as well.
        (self.repo / "connectivity" / "norwayeast.yaml").write_text(HUB + "bastion: true\nnames:\n  bastion_public_ip: pip-demo-hub-afw-pd-nwe-01\n")
        self.assertIn("firewall_public_ip and bastion_public_ip would both be pip-demo-hub-afw-pd-nwe-01", self.problems(GOVERNANCE))

    def test_a_network_s_regions_share_its_group(self):
        (self.repo / "connectivity" / "norwayeast.yaml").write_text(HUB)
        (self.repo / "connectivity" / "swedencentral.yaml").write_text("platform_address_space: 10.200.0.0/21\nlanding_zone_ranges: 10.200.0.0/16\n")
        (self.repo / "identity").mkdir()
        for region in ("norwayeast", "swedencentral"):
            (self.repo / "identity" / f"{region}.yaml").write_text("")
        governance = {**GOVERNANCE, "platform_subscriptions": {**GOVERNANCE["platform_subscriptions"], "identity": "i"}}
        build(self.repo, governance)
        no_region = {"templates": {kind: "{type}-{org}-{app}-{purpose}-{env}-{nn}" for kind in ("virtual_network", "network_security_group")}}
        message = self.problems({**governance, "naming": no_region})
        self.assertIn("norwayeast and swedencentral would both be vnet-demo-idn-pd-01, and Azure needs identity's networks in its regions named apart", message)
        self.assertIn("norwayeast/domain-controllers and swedencentral/domain-controllers would both be nsg-demo-idn-domain-controllers-pd-01", message)
        # Identity makes no GitHub network settings: one name for them in both regions is no clash.
        build(self.repo, {**governance, "naming": {"templates": {"github_network_settings": "{type}-{org}-{app}-{env}-{nn}"}}})

    def test_the_connectivity_subscription_s_groups(self):
        (self.repo / "connectivity" / "norwayeast.yaml").write_text(HUB)
        without_app = {**GOVERNANCE, "naming": {"templates": {"resource_group": "{type}-{org}-{env}-{region}-{nn}"}}}
        self.assertIn("connectivity/norwayeast's and the DNS zones' would both be rg-demo-pd-nwe-01, and Azure needs resource groups in the connectivity subscription",
                      self.problems(without_app))


if __name__ == "__main__":
    unittest.main()
