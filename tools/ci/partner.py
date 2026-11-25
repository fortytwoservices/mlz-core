"""partner.py: Fortytwo's partner ID linked to the identity this apply job signs in as (Partner Admin Link,
docs/pipelines.md, Partner attribution), before the apply. Linked once; every apply after that only reads the link.
An identity linked to another partner ID is moved to Fortytwo's, and a notice names the ID it had.

The identity signs in as Terraform does: the job's GitHub OIDC token, exchanged through the identity's federated
credential for a token to Azure Resource Manager (ARM_CLIENT_ID, ARM_TENANT_ID). Python's standard library only: no
az extension to install, and no host the apply does not reach already.

Never stops the apply: whatever fails is a warning with its reason, and the step exits 0.
"""

from __future__ import annotations

import json
import os
import sys
import urllib.error
import urllib.parse
import urllib.request
from typing import Callable

# Fortytwo's partner ID (Partner Center: Fortytwo Technologies AS), carried over from the landing zone pipeline before MLZ.
PARTNER = "2103291"
PARTNERS = "https://management.azure.com/providers/Microsoft.ManagementPartner/partners"
VERSION = "api-version=2018-02-01"
AUDIENCE = "api://AzureADTokenExchange"  # the federated credentials' audience (bootstrap/terraform, vending)
TIMEOUT = 30
TITLE = "Partner Admin Link"


class Failed(Exception):
    pass


def send(method: str, url: str, headers: dict | None = None, data: bytes | None = None) -> tuple[int, dict]:
    """An HTTPS call: its status and its JSON (empty when it has none). Never raises for an HTTP status."""
    request = urllib.request.Request(url, data=data, headers=headers or {}, method=method)
    try:
        with urllib.request.urlopen(request, timeout=TIMEOUT) as answer:
            status, body = answer.status, answer.read()
    except urllib.error.HTTPError as error:
        with error:
            status, body = error.code, error.read()
    try:
        return status, json.loads(body) if body.strip() else {}
    except ValueError:
        return status, {}


def reason(answer: dict) -> str:
    """What an error answer says: Entra's error_description, or Azure Resource Manager's error code and message."""
    error = answer.get("error")
    if isinstance(error, dict):
        return f"{error.get('code', '')}: {error.get('message', '')}".strip(": ")
    return str(answer.get("error_description") or error or answer.get("message") or "no reason given").splitlines()[0]


def azure_token(env: dict, http: Callable = send) -> str:
    """A token to Azure Resource Manager for ARM_CLIENT_ID, from the job's GitHub OIDC token (the client credentials flow
    with a federated credential)."""
    for name in ("ACTIONS_ID_TOKEN_REQUEST_URL", "ACTIONS_ID_TOKEN_REQUEST_TOKEN", "ARM_CLIENT_ID", "ARM_TENANT_ID"):
        if not env.get(name):
            raise Failed(f"{name} is not set (the job needs id-token: write and the identity's variables)")
    status, github = http("GET", f"{env['ACTIONS_ID_TOKEN_REQUEST_URL']}&audience={urllib.parse.quote(AUDIENCE, safe='')}",
                          {"Authorization": f"bearer {env['ACTIONS_ID_TOKEN_REQUEST_TOKEN']}"})
    if status != 200 or not github.get("value"):
        raise Failed(f"GitHub's OIDC token: HTTP {status}, {reason(github)}")
    form = urllib.parse.urlencode({
        "client_id": env["ARM_CLIENT_ID"],
        "scope": "https://management.azure.com/.default",
        "client_assertion_type": "urn:ietf:params:oauth:client-assertion-type:jwt-bearer",
        "client_assertion": github["value"],
        "grant_type": "client_credentials",
    }).encode()
    status, entra = http("POST", f"https://login.microsoftonline.com/{env['ARM_TENANT_ID']}/oauth2/v2.0/token",
                         {"Content-Type": "application/x-www-form-urlencoded"}, form)
    if status != 200 or not entra.get("access_token"):
        raise Failed(f"Entra ID's token for {env['ARM_CLIENT_ID']}: HTTP {status}, {reason(entra)}")
    return entra["access_token"]


def link(token: str, http: Callable = send, say=print) -> None:
    """Reads the identity's link, and makes it Fortytwo's when it is not: PUT when there is none (404
    ManagementPartnerNotFound), PATCH on Fortytwo's ID when it is another (what az managementpartner update sends)."""
    headers = {"Authorization": f"Bearer {token}", "Accept": "application/json"}  # no body, as the az extension sends
    status, found = http("GET", f"{PARTNERS}?{VERSION}", headers)
    if status == 200:
        current = str((found.get("properties") or {}).get("partnerId") or "")
        if not current:
            raise Failed("reading the link: an answer without a partner ID")
    elif status == 404 and reason(found).startswith("ManagementPartnerNotFound"):
        current = ""
    else:
        raise Failed(f"reading the link: HTTP {status}, {reason(found)}")
    if current == PARTNER:
        say(f"{TITLE}: this identity is linked to Fortytwo's partner ID {PARTNER}.")
        return
    method = "PATCH" if current else "PUT"
    status, done = http(method, f"{PARTNERS}/{PARTNER}?{VERSION}", headers)
    if status != 200:
        raise Failed(f"{method} {PARTNER}: HTTP {status}, {reason(done)}")
    if current:
        say(f"::notice title={TITLE}::This identity was linked to partner ID {current}; it is linked to Fortytwo's {PARTNER} now.")
    else:
        say(f"::notice title={TITLE}::This identity is linked to Fortytwo's partner ID {PARTNER} now.")


def main(env: dict | None = None, http: Callable = send, say=print) -> int:
    try:
        link(azure_token(os.environ if env is None else env, http), http, say)
    except Exception as error:  # noqa: BLE001 - nothing here may stop the apply
        say(f"::warning title={TITLE}::Fortytwo's partner ID could not be linked to this identity, and the apply goes on: {error}")
    return 0


if __name__ == "__main__":
    sys.stdout.reconfigure(line_buffering=True)  # each line reaches the job's log in order (finding 19)
    sys.exit(main())
