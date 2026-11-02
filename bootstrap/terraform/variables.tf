variable "customer_file" {
  type        = string
  description = "The governance repo's customer.yaml: the root ID and display name, the primary region, the platform subscriptions, and parent_management_group_id when the tree goes below an existing group."
  nullable    = false
}

variable "bootstrap_file" {
  type        = string
  description = "The governance repo's bootstrap.yaml: the customer's GitHub organization, the three repos, who approves applies, and who may run Terraform by hand."
  nullable    = false
}
