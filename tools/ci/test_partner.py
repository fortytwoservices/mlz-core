"""Tests for partner.py: the link made, kept or moved to Fortytwo's partner ID, and every failure a warning that never stops
the apply. The answers are Azure's as a user-assigned identity got them in a test tenant on 25 November 2026 (no link:
404 ManagementPartnerNotFound; a link: 200 with properties.partnerId)."""

from __future__ import annotations

import http.server
import json
import threading
import unittest
import urllib.error
import urllib.parse

from ci import partner

ENV = {
    "ACTIONS_ID_TOKEN_REQUEST_URL": "https://token.actions.example/id?api-version=2.0",
    "ACTIONS_ID_TOKEN_REQUEST_TOKEN": "request-token",
    "ARM_CLIENT_ID": "11111111-2222-3333-4444-555555555555",
    "ARM_TENANT_ID": "99999999-8888-7777-6666-555555555555",
}
LINKS = f"{partner.PARTNERS}?{partner.VERSION}"
FORTYTWO = f"{partner.PARTNERS}/{partner.PARTNER}?{partner.VERSION}"
NO_LINK = (404, {"error": {"code": "ManagementPartnerNotFound", "message": "This user or service principal is not linked with a Partner ID"}})


def linked_to(partner_id: str) -> tuple[int, dict]:
    return 200, {"name": partner_id, "properties": {"partnerId": partner_id, "state": "Active"}}


class FakeHttp:
    """Answers by method and URL (an exception is raised); records each call as (method, url, headers, data)."""

    def __init__(self, arm: dict):
        self.calls: list[tuple] = []
        self.answers = {
            ("GET", f"{ENV['ACTIONS_ID_TOKEN_REQUEST_URL']}&audience=api%3A%2F%2FAzureADTokenExchange"): (200, {"value": "github-jwt"}),
            ("POST", f"https://login.microsoftonline.com/{ENV['ARM_TENANT_ID']}/oauth2/v2.0/token"): (200, {"access_token": "arm-token"}),
            **arm,
        }

    def __call__(self, method, url, headers=None, data=None):
        self.calls.append((method, url, headers or {}, data))
        answer = self.answers[(method, url)]
        if isinstance(answer, BaseException):
            raise answer
        return answer

    def arm(self) -> list[tuple[str, str]]:
        return [(method, url) for method, url, _, _ in self.calls if url.startswith(partner.PARTNERS)]


def run(arm: dict, env: dict = ENV) -> tuple[FakeHttp, list[str], int]:
    http, said = FakeHttp(arm), []
    code = partner.main(dict(env), http, said.append)
    return http, said, code


class LinkTest(unittest.TestCase):
    def test_an_identity_with_no_link_is_linked_to_fortytwo(self):
        http, said, code = run({("GET", LINKS): NO_LINK, ("PUT", FORTYTWO): linked_to(partner.PARTNER)})
        self.assertEqual((code, http.arm()), (0, [("GET", LINKS), ("PUT", FORTYTWO)]))
        self.assertEqual(said, ["::notice title=Partner Admin Link::This identity is linked to Fortytwo's partner ID 2103291 now."])
        # The PUT has no body, as az managementpartner create sends it, and goes with the Resource Manager token.
        method, _, headers, data = http.calls[-1]
        self.assertEqual((method, data, headers["Authorization"]), ("PUT", None, "Bearer arm-token"))

    def test_an_identity_linked_to_fortytwo_is_only_read(self):
        http, said, code = run({("GET", LINKS): linked_to("2103291")})
        self.assertEqual((code, http.arm()), (0, [("GET", LINKS)]))
        self.assertEqual(said, ["Partner Admin Link: this identity is linked to Fortytwo's partner ID 2103291."])

    def test_an_identity_linked_to_another_partner_is_moved_and_the_notice_names_the_old_id(self):
        http, said, code = run({("GET", LINKS): linked_to("1234567"), ("PATCH", FORTYTWO): linked_to(partner.PARTNER)})
        self.assertEqual((code, http.arm()), (0, [("GET", LINKS), ("PATCH", FORTYTWO)]))
        self.assertEqual(said, ["::notice title=Partner Admin Link::This identity was linked to partner ID 1234567; it is linked to Fortytwo's 2103291 now."])

    def test_the_identity_signs_in_through_its_federated_credential(self):
        http, _, _ = run({("GET", LINKS): linked_to("2103291")})
        (_, _, github_headers, _), (_, token_url, token_headers, form) = http.calls[:2]
        self.assertEqual(github_headers, {"Authorization": "bearer request-token"})
        self.assertEqual(token_headers, {"Content-Type": "application/x-www-form-urlencoded"})
        self.assertEqual(dict(urllib.parse.parse_qsl(form.decode())), {
            "client_id": ENV["ARM_CLIENT_ID"], "scope": "https://management.azure.com/.default",
            "client_assertion_type": "urn:ietf:params:oauth:client-assertion-type:jwt-bearer", "client_assertion": "github-jwt",
            "grant_type": "client_credentials"})


class FailureTest(unittest.TestCase):
    """Whatever fails, one warning with the reason, exit 0, and nothing written after it."""

    def assertWarns_(self, said: list[str], reason: str):
        self.assertEqual(len(said), 1, said)
        self.assertTrue(said[0].startswith("::warning title=Partner Admin Link::Fortytwo's partner ID could not be linked to this "
                                           "identity, and the apply goes on: "), said[0])
        self.assertIn(reason, said[0])
        self.assertNotIn("github-jwt", said[0])
        self.assertNotIn("arm-token", said[0])

    def test_without_the_job_s_oidc_token(self):
        env = {key: value for key, value in ENV.items() if key != "ACTIONS_ID_TOKEN_REQUEST_URL"}
        http, said, code = run({}, env)
        self.assertEqual((code, http.calls), (0, []))
        self.assertWarns_(said, "ACTIONS_ID_TOKEN_REQUEST_URL is not set (the job needs id-token: write")

    def test_when_entra_id_refuses_the_federated_credential(self):
        http = FakeHttp({})
        http.answers[("POST", f"https://login.microsoftonline.com/{ENV['ARM_TENANT_ID']}/oauth2/v2.0/token")] = (
            400, {"error": "invalid_client", "error_description": "AADSTS700213: No matching federated identity record found.\r\nTrace ID: x"})
        said = []
        self.assertEqual(partner.main(dict(ENV), http, said.append), 0)
        self.assertWarns_(said, "Entra ID's token for 11111111-2222-3333-4444-555555555555: HTTP 400, AADSTS700213: No matching federated identity record found.")
        self.assertEqual(http.arm(), [])

    def test_when_azure_refuses_the_read_or_the_write(self):
        forbidden = (403, {"error": {"code": "AuthorizationFailed", "message": "The client does not have authorization."}})
        for arm, writes, reason in (
            ({("GET", LINKS): forbidden}, [], "reading the link: HTTP 403, AuthorizationFailed: The client does not have authorization."),
            ({("GET", LINKS): NO_LINK, ("PUT", FORTYTWO): forbidden}, ["PUT"], "PUT 2103291: HTTP 403, AuthorizationFailed"),
            ({("GET", LINKS): linked_to("1234567"), ("PATCH", FORTYTWO): (500, {})}, ["PATCH"], "PATCH 2103291: HTTP 500, no reason given"),
            ({("GET", LINKS): (200, {})}, [], "reading the link: an answer without a partner ID"),
        ):
            with self.subTest(reason=reason):
                http, said, code = run(arm)
                self.assertEqual(code, 0)
                self.assertEqual([method for method, _ in http.arm()], ["GET", *writes])
                self.assertWarns_(said, reason)

    def test_when_the_network_fails_or_anything_else_goes_wrong(self):
        for error in (TimeoutError("The read operation timed out"), urllib.error.URLError("Connection refused"), KeyError("value")):
            with self.subTest(error=repr(error)):
                http, said, code = run({("GET", LINKS): error})
                self.assertEqual(code, 0)
                self.assertWarns_(said, str(error))


class SendTest(unittest.TestCase):
    """send against a local server: the status and JSON of any answer, and a write without a body sent as the az
    extension sends it (Content-Length 0, no Content-Type)."""

    def serve(self, status: int, body: bytes):
        seen = {}

        class Handler(http.server.BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def answer(self):
                seen.update(method=self.command, headers=dict(self.headers))
                self.send_response(status)
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
            do_GET = do_PUT = do_PATCH = answer

        server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        self.addCleanup(server.server_close)
        self.addCleanup(server.shutdown)
        return f"http://127.0.0.1:{server.server_port}/x", seen

    def test_any_status_comes_back_with_its_json(self):
        url, _ = self.serve(404, json.dumps(NO_LINK[1]).encode())
        self.assertEqual(partner.send("GET", url), NO_LINK)
        url, _ = self.serve(502, b"<html>Bad Gateway</html>")
        self.assertEqual(partner.send("GET", url), (502, {}))

    def test_a_write_without_a_body(self):
        for method in ("PUT", "PATCH"):
            with self.subTest(method=method):
                url, seen = self.serve(200, b'{"properties": {"partnerId": "2103291"}}')
                self.assertEqual(partner.send(method, url, {"Accept": "application/json"})[0], 200)
                headers = {key.lower(): value for key, value in seen["headers"].items()}
                self.assertEqual((seen["method"], headers.get("content-length"), headers.get("content-type")), (method, "0", None))


if __name__ == "__main__":
    unittest.main()
