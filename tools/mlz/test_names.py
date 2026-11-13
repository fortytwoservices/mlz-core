"""Tests for the platform's, governance's and the bootstrap's names. Run from tools/: python3 -m unittest discover -s . -t ."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from mlz import names, naming
from mlz.merge import ConfigError
from mlz.platform import build
from mlz.test_platform import GOVERNANCE, HUB

FLAT = "{type}-{org}-{app}-{env}-{region}-{nn}"  # a template without {purpose}


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
        for call in (lambda: names.governance(settings(template=FLAT), "norwayeast"), lambda: names.bootstrap(settings(template=FLAT), "norwayeast")):
            with self.assertRaisesRegex(ConfigError, "governance's management group and management's network group would both be rg-acme-mgt-pd-nwe-01, "
                                                     "and Azure needs resource groups in the management subscription named apart"):
                call()
        # The bootstrap's own group, named explicitly onto governance's: refused too.
        with self.assertRaisesRegex(ConfigError, "governance's management group and the bootstrap's group would both be RG-acme-mgt-pd-nwe-01"):
            names.bootstrap(settings(), "norwayeast", {"resource_group": "RG-acme-mgt-pd-nwe-01"})

    def test_data_collection_rules(self):
        with self.assertRaisesRegex(ConfigError, "change_tracking and vm_insights would both be dcr-acme-mgt-pd-nwe-01"):
            names.governance(settings(templates={"data_collection_rule": FLAT}), "norwayeast")

    def test_network_security_groups(self):
        with self.assertRaisesRegex(ConfigError, "github-runners and runners would both be nsg-acme-mgt-pd-nwe-01, and Azure needs management's network security groups"):
            names.network(settings(templates={"network_security_group": FLAT}), "management", "norwayeast", ["github-runners", "runners"])

    def test_the_pipeline_identities(self):
        with self.assertRaisesRegex(ConfigError, "governance-plan and governance-apply would both be id-acme-mlz-pd-nwe-01, and Azure needs the pipeline identities"):
            names.bootstrap(settings(templates={"user_assigned_identity": FLAT}), "norwayeast")

    def test_the_defaults_are_apart(self):
        names.governance(settings(), "norwayeast")
        names.bootstrap(settings(), "norwayeast")
        names.network(settings(), "management", "norwayeast", ["github-runners", "runners", "private-endpoints"])


class HubApartTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.repo = Path(self.tmp.name)
        (self.repo / "hubs").mkdir()

    def tearDown(self):
        self.tmp.cleanup()

    def problems(self, governance: dict) -> str:
        with self.assertRaises(ConfigError) as caught:
            build(self.repo, governance)
        return str(caught.exception)

    def test_the_public_ips_a_hub_has(self):
        flat_ips = {**GOVERNANCE, "naming": {"templates": {"public_ip_address": FLAT}}}
        (self.repo / "hubs" / "norwayeast.yaml").write_text(HUB)
        build(self.repo, flat_ips)  # the firewall's alone: nothing to tell apart
        (self.repo / "hubs" / "norwayeast.yaml").write_text(HUB + "bastion: true\n")
        self.assertIn("firewall_public_ip and bastion_public_ip would both be pip-demo-hub-pd-nwe-01, and Azure needs hubs/norwayeast's public IP addresses",
                      self.problems(flat_ips))
        (self.repo / "hubs" / "norwayeast.yaml").write_text(HUB + "firewall: Basic\n")
        self.assertIn("firewall_public_ip and firewall_management_public_ip would both be", self.problems(flat_ips))
        # Named explicitly onto another: refused as well.
        (self.repo / "hubs" / "norwayeast.yaml").write_text(HUB + "bastion: true\nnames:\n  bastion_public_ip: pip-demo-hub-afw-pd-nwe-01\n")
        self.assertIn("firewall_public_ip and bastion_public_ip would both be pip-demo-hub-afw-pd-nwe-01", self.problems(GOVERNANCE))

    def test_a_network_s_regions_share_its_group(self):
        (self.repo / "hubs" / "norwayeast.yaml").write_text(HUB + "identity: true\n")
        (self.repo / "hubs" / "swedencentral.yaml").write_text("platform_address_space: 10.200.0.0/21\nlanding_zone_ranges: 10.200.0.0/16\nidentity: true\n")
        governance = {**GOVERNANCE, "platform_subscriptions": {**GOVERNANCE["platform_subscriptions"], "identity": "i"}}
        build(self.repo, governance)
        no_region = {"templates": {kind: "{type}-{org}-{app}-{purpose}-{env}-{nn}" for kind in ("virtual_network", "network_security_group")}}
        message = self.problems({**governance, "naming": no_region})
        self.assertIn("norwayeast and swedencentral would both be vnet-demo-idn-pd-01, and Azure needs identity's networks in its regions named apart", message)
        self.assertIn("norwayeast/domain-controllers and swedencentral/domain-controllers would both be nsg-demo-idn-domain-controllers-pd-01", message)
        # Identity makes no GitHub network settings: one name for them in both regions is no clash.
        build(self.repo, {**governance, "naming": {"templates": {"github_network_settings": "{type}-{org}-{app}-{env}-{nn}"}}})

    def test_the_connectivity_subscription_s_groups(self):
        (self.repo / "hubs" / "norwayeast.yaml").write_text(HUB)
        without_app = {**GOVERNANCE, "naming": {"templates": {"resource_group": "{type}-{org}-{env}-{region}-{nn}"}}}
        self.assertIn("hubs/norwayeast's and the DNS zones' would both be rg-demo-pd-nwe-01, and Azure needs resource groups in the connectivity subscription",
                      self.problems(without_app))


if __name__ == "__main__":
    unittest.main()
