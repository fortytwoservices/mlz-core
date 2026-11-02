variable "config_file" {
  type        = string
  description = "The platform config written by `python3 -m mlz platform` from the customer's hubs/*.yaml and the governance contract."
  nullable    = false
}
