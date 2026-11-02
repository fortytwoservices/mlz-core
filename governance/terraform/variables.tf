variable "rendered_library_path" {
  type        = string
  description = "The output folder of scripts/render_library.py for this customer: its library files and config.json."
  nullable    = false
}

variable "platform_contract_file" {
  type        = string
  default     = null
  description = <<-EOT
    The platform's published contract, `_generated/platform.yaml`, once the platform has been applied. Until then the
    Private Link DNS policy keeps the library's placeholders, so no rights are granted on zones that do
    not exist yet; the next run after the platform publishes its contract points it at the real zones.
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
