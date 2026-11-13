variable "rendered_library_path" {
  type        = string
  description = "The output folder of scripts/render_library.py for this customer: its library files and config.json."
  nullable    = false
}

variable "bootstrap_contract_file" {
  type        = string
  default     = null
  description = <<-EOT
    The bootstrap's contract, `_generated/bootstrap.yaml`: the pipeline identities. Governance grants the
    vending identities their rights on the Landing Zones and Sandboxes groups it creates. Without it,
    for example when someone deploys by hand, no such rights are granted.
  EOT
}

variable "policy_default_values" {
  type        = map(string)
  default     = {}
  description = <<-EOT
    ALZ policy default values that replace the ones this root sets. Same shape as the ALZ module's input:
    the default name, and a JSON object with one `value` attribute.
  EOT
  nullable    = false
}
