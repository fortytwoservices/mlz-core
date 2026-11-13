variable "root_id" {
  type        = string
  description = "The customer's root ID from customer.yaml, the prefix of every management group."
  nullable    = false
}

variable "landing_zone" {
  type        = string
  default     = null
  description = "(Optional) A landing zone's key (`<workload>-<env>`), for the names in its subscription."
}

variable "region" {
  type        = string
  description = "Azure region of the named resources, for example `norwayeast`."
  nullable    = false

  validation {
    condition     = contains(keys(jsondecode(file("${path.module}/region-codes.json"))), var.region)
    error_message = "`region` is not an Azure region in modules/naming/region-codes.json (lower case, no spaces, for example `norwayeast`)."
  }
}
