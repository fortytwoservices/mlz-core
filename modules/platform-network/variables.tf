variable "names" {
  type = object({
    virtual_network        = string
    route_table            = string
    peering_to_hub         = string
    peering_from_hub       = string
    subnet                 = string
    network_security_group = string
  })
  description = "The network's names in its region, from modules/naming's `platform_networks`."
  nullable    = false
}

variable "location" {
  type        = string
  description = "The network's region, its hub's."
  nullable    = false
}

variable "parent_id" {
  type        = string
  description = "Resource ID of the resource group the bootstrap made for this network."
  nullable    = false
}

variable "address_space" {
  type        = string
  description = "The network's /24 in its region's platform_address_space."
  nullable    = false
}

variable "subnets" {
  type        = map(string)
  description = "The network's subnets: name to address range."
  nullable    = false
}

variable "dns_servers" {
  type        = list(string)
  description = "What the network resolves through: the hub's firewall, or its resolver behind a Basic firewall. Empty is Azure's DNS."
  nullable    = false
}

variable "firewall" {
  type        = string
  description = "The hub firewall's private address, where everything leaving the network goes."
  nullable    = false
}

variable "hub_virtual_network_id" {
  type        = string
  description = "Resource ID of the hub's virtual network, which the network peers with from both sides."
  nullable    = false
}

variable "tags" {
  type        = map(string)
  description = "Tags on everything the module creates."
  nullable    = false
}

variable "github_runners" {
  type = object({
    subnet      = string
    name        = string
    business_id = string
  })
  description = "Where GitHub's hosted runners join the network: the subnet delegated to GitHub, the network settings resource's name, and the GitHub organization's ID. Null for none."
  default     = null
}
