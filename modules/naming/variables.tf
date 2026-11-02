variable "root_id" {
  type        = string
  description = "The customer's root ID from customer.yaml, the prefix of every management group."
  nullable    = false
}

variable "region" {
  type        = string
  description = "Azure region of the named resources, for example `norwayeast`."
  nullable    = false

  validation {
    condition     = contains(["norwayeast", "norwaywest", "westeurope", "northeurope", "swedencentral"], var.region)
    error_message = "`region` has no short code yet; add it to modules/naming/main.tf."
  }
}
