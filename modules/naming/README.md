# Naming data

What mlz-core names things with (`docs/naming.md`), read by `tools/mlz/naming.py`:

- `region-codes.json`: every Azure region and Microsoft's code for it, as in `vnet-acme-pay-pd-nwe-01`. It is
  `Azure/avm-utl-regions` v0.13.0's geo codes. A code is in resource names, so one never changes once used.
- `name-rules.json`: each type's short name and Azure's naming rules for it (length, case, dashes, characters).
  `tools/dev/name_rules.py` writes it from `Azure/avm-utl-naming`'s catalog, with mlz-core's own rule where
  Microsoft publishes none.

Until v0.5.0 this folder was also a Terraform module that gave every root its names. The Python loaders give them
now (`tools/mlz/names.py`, `tools/mlz/vending.py`), so its Terraform is gone.
