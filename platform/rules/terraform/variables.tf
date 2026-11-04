variable "config_file" {
  type        = string
  description = "The rules config written by `python3 -m mlz firewall` from the platform repo's firewall/ folder and the vending contract."
  nullable    = false
}
