variable "config_file" {
  type        = string
  description = "The platform config written by `python3 -m mlz platform`, the one the hub's root reads."
  nullable    = false
}

variable "network" {
  type        = string
  description = "Which stack this is: `management` or `identity`. Each has a state of its own."
  nullable    = false

  validation {
    condition     = contains(["management", "identity"], var.network)
    error_message = "`network` is management or identity."
  }
}
