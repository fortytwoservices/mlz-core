# A name from the customer's template, as mlz-core's own renderer gives it (tools/mlz/naming.py), for the resources a
# team makes in its workload repo: the same template, the same codes and the same rules file, so its Application
# Gateway is named as vending names its network. A name that does not fit is refused, never cut.
locals {
  rules = jsondecode(file("${path.module}/name-rules.json")).types[var.type]

  # The template as parts: a token in braces, a dash, or plain text.
  template = lookup(var.mlz.templates, var.type, var.mlz.template)
  parts = [for found in regexall("\\{([a-z]+)\\}|(-)|([a-z0-9]+)", local.template) : (
    found[0] != null ? { kind = "token", value = found[0] } : found[1] != null ? { kind = "dash", value = "-" } : { kind = "text", value = found[2] }
  )]

  # A type without dashes drops a purpose's too.
  given   = var.purpose == null ? "" : var.purpose
  purpose = local.rules.dashes ? local.given : replace(local.given, "-", "")
  values = {
    type    = local.rules.slug
    org     = var.mlz.org
    app     = var.mlz.app
    purpose = local.purpose
    env     = var.mlz.env
    region  = var.mlz.region
    nn      = format("%02d", var.nn)
  }

  # An empty {purpose} goes, with the dash before it, or the one after it when it comes first.
  empty = [for i, part in local.parts : i if part.kind == "token" && part.value == "purpose" && local.purpose == ""]
  dropped = flatten([for i in local.empty : (
    i > 0 && local.parts[max(i - 1, 0)].kind == "dash" ? [i - 1, i] :
    i + 1 < length(local.parts) && local.parts[min(i + 1, length(local.parts) - 1)].kind == "dash" ? [i, i + 1] : [i]
  )])
  kept = [for i, part in local.parts : part if !contains(local.dropped, i)]

  joined = join("", [for part in local.kept : (
    part.kind == "token" ? local.values[part.value] : part.kind == "dash" ? (local.rules.dashes ? "-" : "") : part.value
  )])
  name = local.rules.lowercase ? lower(local.joined) : local.joined

  # Azure's rules for the type: its length, and its characters, as a pattern or as Unicode categories.
  allowed = try(local.rules.allowed, null)
  pattern = local.allowed == null ? local.rules.pattern : format("^[%s%s]+$",
    join("", [for category in local.allowed.categories : "\\p{${category}}"]),
    join("", [for c in split("", local.allowed.characters) : contains(["-", "]", "\\", "^"], c) ? "\\${c}" : c]),
  )
  min_length = coalesce(local.rules.min_length, 1)
  problems = compact([
    length(local.name) > local.rules.max_length ? "${var.type} name ${local.name} is ${length(local.name)} characters, ${length(local.name) - local.rules.max_length} more than Azure allows; shorten a short name, give the type a template of its own in customer.yaml, or set this name explicitly." : "",
    length(local.name) < local.min_length ? "${var.type} name ${local.name} is ${length(local.name)} characters; Azure needs at least ${local.min_length}." : "",
    !can(regex(local.pattern, local.name)) || (try(local.rules.no_double_hyphen, false) && strcontains(local.name, "--")) ? "${var.type} name ${local.name} has characters Azure does not allow there." : "",
    try(local.rules.no_trailing_period, false) && endswith(local.name, ".") ? "${var.type} name ${local.name} ends with a period, which Azure does not allow." : "",
  ])
}
