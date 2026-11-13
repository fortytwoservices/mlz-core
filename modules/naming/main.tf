# Names of everything mlz-core creates, so every root derives the same name from customer.yaml and no
# root has to wait for another to be applied before it knows a name or a resource ID.
#
# Pattern: <type>-<root_id>-<purpose>-<region code>, with the CAF abbreviation as <type>.

locals {
  # Microsoft's geo codes (Azure/avm-utl-regions), the same ones the az-tf-mlz connectivity modules put
  # in their names, so a customer's names share one set of codes. A region missing here is an error.
  # region-codes.json is avm-utl-regions v0.13.0's locals.geo.codes.tf.json, every region; mlz-core's Python
  # reads the same file. A code is in resource names, so one never changes once used.
  region_codes = jsondecode(file("${path.module}/region-codes.json"))
  region       = local.region_codes[var.region]

  suffix = "${var.root_id}-%s-${local.region}"
}
