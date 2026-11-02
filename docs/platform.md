# Platform

The `mlz-platform` role: the hub network in each region, its firewall, DNS resolver and Bastion, and the
Private Link DNS zones. Built on `az-tf-mlz-connectivity-hub` (hub-and-spoke; Virtual WAN is the
alternative, through `az-tf-mlz-connectivity-vwan`).

## What the customer writes

One file per region, `hubs/<region>.yaml`, saying what the hub should have:

```yaml
# The hub network in Norway East. Landing zones peer to it, and their traffic goes through its firewall.

address_space: 10.100.0.0/22          # the hub itself; its subnets are carved from this automatically
landing_zone_ranges: 10.100.0.0/16    # where landing zone networks get their address ranges

firewall: Standard                    # Standard, Premium or Basic
dns_resolver: true                    # resolves Private Link names for landing zones and on-premises
bastion: false                        # Bastion for VMs in any landing zone
```

A hub can also be a folder of chunks, `hubs/<region>/*.yaml`, read as one file; a value set in two
chunks is an error that names both files.

Defaults: `firewall: Standard`, `dns_resolver: true`, `bastion: false`. The primary region (from
governance) must have a hub.

## What is worked out

- **Subnets**, by one fixed layout inside `address_space` (a /23 or larger, /22 usual): firewall /26 first,
  firewall management /26 second (Basic only), gateway /27, Bastion /26 at the start of the second /24,
  then the DNS resolver's inbound and outbound /28s. Every hub looks the same.
- **Names**, `<type>-<root>-hub-<region code>` (for example `afw-mlz-hub-nwe`), and the Private Link zones'
  group `rg-<root>-dns-<region code>`, the one governance's DNS policy points at.

`python3 -m mlz platform` reads `hubs/` and `_generated/governance.yaml`, checks both, and writes the
root's config (JSON, for one run, never committed); the Terraform root only reads that file.

## Contract

The `contract` output, written as `_generated/platform.yaml` in the governance and vending repos by
`python3 -m mlz contract platform`: the hub's
connectivity (peering target, firewall address, DNS servers, the spoke routes), the hubs' own ranges
(which the IP allocator never hands out), and where the Private Link zones are.
