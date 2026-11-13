variable "mlz" {
  type = object({
    org       = string                    # the customer's code, as in every name: acme
    app       = string                    # the workload's short name: pay
    env       = string                    # the environment's code: pd
    region    = string                    # the region's code: nwe
    template  = string                    # customer.yaml's naming.template, or mlz-core's default
    templates = optional(map(string), {}) # a type's own, by type
  })
  description = "The landing zone's naming values, as vending writes them to the workload repo's mlz.auto.tfvars.json."
  nullable    = false
}

variable "type" {
  type        = string
  description = "The resource's type, one of name-rules.json's: application_gateway, key_vault, storage_account, ..."
  nullable    = false

  validation {
    condition     = contains(keys(jsondecode(file("${path.module}/name-rules.json")).types), var.type)
    error_message = "`type` is not a type mlz-core names; name-rules.json lists them."
  }
}

variable "purpose" {
  type        = string
  default     = null
  description = "What the resource is for, where the landing zone has more than one of its type: web, logs. Lower-case letters, digits and inner hyphens, up to 20. Empty, it drops out of the name with one dash."

  validation {
    condition     = var.purpose == null || can(regex("^[a-z0-9]([a-z0-9-]{0,18}[a-z0-9])?$", var.purpose))
    error_message = "`purpose` is 1 to 20 lower-case letters and digits, with hyphens inside."
  }
}

variable "nn" {
  type        = number
  default     = 1
  description = "Which one of the same role, 1 to 99: 01, 02."
  nullable    = false

  validation {
    condition     = var.nn >= 1 && var.nn <= 99 && floor(var.nn) == var.nn
    error_message = "`nn` is a whole number, 1 to 99."
  }
}
