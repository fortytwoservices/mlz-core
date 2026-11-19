# What the hub module is given for a hub with advanced: and one without, with the provider mocked: no Azure.
# Run: terraform -chdir=platform/terraform test. tests/advanced.json is what python3 -m mlz platform writes for
# connectivity/norwayeast.yaml (no advanced:) and connectivity/swedencentral.yaml (with it); test_advanced.py checks
# that it still is.
mock_provider "azapi" {
  # The hub module's region lookups list Azure's locations: the two hubs' regions are enough.
  mock_data "azapi_resource_action" {
    defaults = {
      output = {
        value = [
          for name in ["norwayeast", "swedencentral"] : {
            name                     = name
            displayName              = name
            availabilityZoneMappings = [{ logicalZone = "1" }, { logicalZone = "2" }, { logicalZone = "3" }]
            metadata                 = { geography = "Europe", geographyGroup = "Europe", regionCategory = "Recommended", regionType = "Physical", pairedRegion = [] }
          }
        ]
      }
    }
  }
}

variables {
  config_file = "tests/advanced.json"
}

run "advanced" {
  command = plan

  # What advanced: sets reaches the hub's objects, beside mlz-core's own values, which it cannot change.
  assert {
    condition = (
      local.hub_virtual_networks["swedencentral"].bastion.sku == "Premium" &&
      local.hub_virtual_networks["swedencentral"].bastion.tunneling_enabled == true &&
      local.hub_virtual_networks["swedencentral"].bastion.name == "bas-demo-hub-pd-sdc-01" &&
      tolist(local.hub_virtual_networks["swedencentral"].firewall.zones) == tolist(["1", "2", "3"]) &&
      local.hub_virtual_networks["swedencentral"].firewall.firewall_policy.threat_intelligence_mode == "Deny" &&
      local.hub_virtual_networks["swedencentral"].firewall.firewall_policy.name == "afwp-demo-hub-pd-sdc-01" &&
      local.hub_virtual_networks["swedencentral"].firewall.sku_tier == "Standard" &&
      tolist(local.hub_virtual_networks["swedencentral"].virtual_network_dns_servers) == tolist(["10.200.1.68"])
    )
    error_message = "advanced: did not reach the hub's objects as given, beside mlz-core's values."
  }

  # A hub without advanced: is given exactly the attributes it was given before advanced: existed.
  assert {
    condition = (
      sort(keys(local.hub_virtual_networks["norwayeast"])) == sort(["location", "address_space", "is_primary", "name", "resource_group_name", "routing_supernet", "firewall", "dns_resolver", "bastion"]) &&
      sort(keys(local.hub_virtual_networks["norwayeast"].firewall)) == sort(["name", "sku_tier", "subnet_address_prefix", "management_subnet_address_prefix", "public_ip_name", "management_public_ip_name", "route_table_name", "firewall_policy"]) &&
      keys(local.hub_virtual_networks["norwayeast"].firewall.firewall_policy) == ["name"] &&
      sort(keys(local.hub_virtual_networks["norwayeast"].bastion)) == sort(["name", "public_ip_name", "subnet_address_prefix"])
    )
    error_message = "A hub without advanced: is not given what it was before."
  }
}
