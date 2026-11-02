variable "config_file" {
  type        = string
  description = "One landing zone's config, `<key>.json`, written by `python3 -m mlz vending`. Each landing zone has a state of its own."
  nullable    = false
}
