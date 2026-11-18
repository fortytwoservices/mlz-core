variable "config_file" {
  type        = string
  description = "The platform config written by `python3 -m mlz platform` from the platform repo's connectivity/, management/ and identity/ folders and the governance contract."
  nullable    = false
}
