variable "config_file" {
  type        = string
  description = "One landing zone's config, `<key>.json`, written by `python3 -m mlz vending`. Each landing zone has a state of its own."
  nullable    = false
}

variable "github_organization" {
  type        = string
  default     = null
  description = "(Optional) The customer's GitHub organization, for the deploy identity's federated credential when the landing zone has a repo."
}
