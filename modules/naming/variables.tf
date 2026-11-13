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

  # The codes as Python's short() takes them (tools/mlz/naming.py, LIMITS): only {purpose} may be empty. Microsoft's
  # region codes are 2 to 4 (region-codes.json), a customer's own 2 to 5.
  validation {
    condition = alltrue([for code, limits in { org = [2, 4], app = [2, 5], env = [1, 4], region = [2, 5] } :
    can(regex(format("^[a-z0-9]{%d,%d}$", limits[0], limits[1]), var.mlz[code]))])
    error_message = "The codes are lower-case letters and digits: org 2 to 4, app 2 to 5, env 1 to 4, region 2 to 5."
  }
  # Templates are checked as customer.yaml's are (tools/mlz/naming.py, parse()), so none renders a name Python would not.
  validation {
    condition = alltrue([for template in concat([var.mlz.template], values(var.mlz.templates)) :
    can(regex("^(?:\\{[a-z]+\\}|-|[a-z0-9]+)+$", template))])
    error_message = "A template is tokens in braces, dashes and lower-case letters and digits, nothing else."
  }
  validation {
    condition = alltrue([for template in concat([var.mlz.template], values(var.mlz.templates)) :
    length(setsubtract(regexall("\\{([a-z]+)\\}", template)[*][0], ["type", "org", "app", "purpose", "env", "region", "nn"])) == 0])
    error_message = "A template's tokens are {type}, {org}, {app}, {purpose}, {env}, {region} and {nn}."
  }
  validation {
    condition = alltrue([for template in concat([var.mlz.template], values(var.mlz.templates)) :
    strcontains(template, "{type}") && !startswith(template, "-") && !endswith(template, "-") && !strcontains(template, "--")])
    error_message = "A template has {type}: every name starts from its resource's short name. It does not start or end with a dash, or have two in a row."
  }
  validation {
    # Each token once, so an empty purpose drops out as it does in Python.
    condition = alltrue([for template in concat([var.mlz.template], values(var.mlz.templates)) :
    length(regexall("\\{[a-z]+\\}", template)) == length(distinct(regexall("\\{[a-z]+\\}", template)))])
    error_message = "A template names each token once."
  }
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
  description = "What the resource is for, where the landing zone has more than one of its type: web, logs. Lower-case letters, digits and inner hyphens, up to 20. Null or empty, it drops out of the name with one dash."

  validation {
    condition     = var.purpose == null || var.purpose == "" || can(regex("^[a-z0-9]([a-z0-9-]{0,18}[a-z0-9])?$", var.purpose))
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
