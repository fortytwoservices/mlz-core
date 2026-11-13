# The landing zone, as vending writes it to mlz.auto.tfvars.json and keeps it up to date. Do not edit that file.
variable "mlz" {
  type = object({
    landing_zone    = string # payments-prod
    subscription_id = string
    location        = string # norwayeast
    # Naming, for mlz-core's helper (modules/naming): the customer's code, the workload's short name, the codes of
    # the environment and the region, and the template.
    org       = string
    app       = string
    env       = string
    region    = string
    template  = string
    templates = map(string)
  })
}
