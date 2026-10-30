variable "rendered_library_path" {
  type        = string
  description = "The output folder of scripts/render_library.py for this customer: its library files and config.json."
  nullable    = false
}

variable "policy_default_values" {
  type        = map(string)
  default     = {}
  description = <<-EOT
    More ALZ policy default values, merged over the ones this root sets. Same shape as the ALZ module's
    input: the default name, and a JSON object with one `value` attribute. For example the Log Analytics
    workspace and the monitoring agent's identity and data collection rules, once management runs in this
    root.
  EOT
  nullable    = false
}
