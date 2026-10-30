"""HTTPS验收工具的隔离HTTP契约回归；模拟网络，不声称公网或TLS已通过。"""

import base64
import contextlib
import copy
import io
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import httpx

from scripts.verify_public_demo import Verification, asset_path, main, public_origin, sha

BASE = "https://review.example.org"
USERNAME = "test-reviewer"
PASSWORD = "isolated-public-demo-test-password"
PDF = b"%PDF-1.4\n" + b"fixture-contract-only\n" * 100
DOCUMENT_ID = sha(PDF)
PREFIX = f"/api/documents/{DOCUMENT_ID}"
TEXT = "正文 [1] 以及资料。\nReferences\n[1] Example paper.\n1 Introduction"


def source(start, end):
    return {
        "document_id": DOCUMENT_ID,
        "page": 1,
        "start": start,
        "end": end,
        "quote": TEXT[start:end],
        "page_sha256": sha(TEXT.encode()),
    }


def navigation():
    mention = TEXT.index("[1]")
    entry = TEXT.index("[1]", mention + 1)
    heading = TEXT.index("1 Introduction")
    return {
        "document_id": DOCUMENT_ID,
        "fingerprint": "e" * 64,
        "offset_encoding": "unicode-code-points",
        "mentions": [source(mention, mention + 3)],
        "entries": [{"number": 1, "evidence": [source(entry, entry + 18)]}],
        "outline": [source(heading, len(TEXT))],
    }


class Service:
    def __init__(self):
        self.requests = []
        self.overrides = {}
        self.links = navigation()
        self.health = {
            "status": "ok",
            "model_ready": True,
            "embedding_ready": True,
            "ocr_ready": True,
        }

    def __call__(self, request):
        self.requests.append(request)
        authenticated = request.headers.get("Authorization") is not None
        path = request.url.path
        override = self.overrides.get((request.method, path, authenticated))
        if override:
            return override(request)
        if not authenticated:
            return httpx.Response(
                401,
                headers={"WWW-Authenticate": 'Basic realm="mock"', "Cache-Control": "no-store"},
                json={"detail": "authentication required"},
            )
        if path == "/":
            return httpx.Response(
                200,
                headers={"Content-Type": "text/html", "Cache-Control": "no-store"},
                text='<script type="module" src="/assets/a.js"></script>'
                '<link rel="stylesheet" href="/assets/a.css">',
            )
        if path.startswith("/assets/"):
            kind = "application/javascript" if path.endswith(".js") else "text/css"
            return httpx.Response(200, headers={"Content-Type": kind}, content=b"sample-asset")
        if path == "/api/health":
            return httpx.Response(200, json=self.health)
        if path == "/api/documents":
            return httpx.Response(
                200,
                json=[
                    {
                        "id": DOCUMENT_ID,
                        "status": "ready",
                        "is_active": 1,
                        "trashed_at": None,
                        "pages": 1,
                    }
                ],
            )
        if path == PREFIX + "/pdf":
            headers = {"Content-Type": "application/pdf", "Content-Length": str(len(PDF))}
            if request.method == "HEAD":
                return httpx.Response(200, headers=headers)
            if request.headers.get("Range"):
                headers.update(
                    {"Content-Range": f"bytes 0-1023/{len(PDF)}", "Content-Length": "1024"}
                )
                return httpx.Response(206, headers=headers, content=PDF[:1024])
            return httpx.Response(200, headers=headers, content=PDF)
        if path == PREFIX + "/render/1":
            return httpx.Response(
                200, headers={"Content-Type": "image/png"}, content=b"\x89PNG\r\n\x1a\nfixture"
            )
        if path == PREFIX + "/reading-links":
            return httpx.Response(200, json=self.links)
        if path == PREFIX + "/pages/1":
            return httpx.Response(200, json={"text": TEXT})
        return httpx.Response(404)


class PublicDemoTests(unittest.TestCase):
    def setUp(self):
        self.service = Service()
        self.verification = Verification(
            BASE, DOCUMENT_ID, USERNAME, PASSWORD, transport=httpx.MockTransport(self.service)
        )
        self.addCleanup(self.verification.close)

    def test_accepts_https_origin_and_rejects_credentials_paths_or_local_literal(self):
        self.assertEqual(public_origin(BASE + "/"), BASE)
        self.assertEqual(public_origin(BASE + ":443"), BASE)
        for invalid in [
            "http://review.example.org",
            BASE + "/demo",
            BASE + "?key=value",
            BASE + "#fragment",
            "https://user:password@review.example.org",
            "https://127.0.0.1",
            "https://192.168.3.193",
            "https://localhost",
            "https://[::1]",
            "https://review.example.org:70000",
            "https://review.example.org\\@other.example",
            "https://review.example.org\n",
        ]:
            with self.subTest(invalid=invalid), self.assertRaises(ValueError):
                public_origin(invalid)

    def test_positive_read_only_contract_checks_pdf_identity_and_unicode_sources(self):
        report = self.verification.run()
        self.assertTrue(report["all_requested_checks_passed"])
        self.assertTrue(report["simulation_transport_used"])
        self.assertFalse(report["independent_external_network_verified"])
        self.assertEqual(len(report["sampled_source_intervals"]), 3)
        self.assertEqual(len(report["frontend_assets"]), 2)
        self.assertTrue(all(r.method in {"GET", "HEAD"} for r in self.service.requests))
        self.assertTrue(all(r.url.host == "review.example.org" for r in self.service.requests))
        serialized = json.dumps(report)
        for secret in [
            PASSWORD,
            USERNAME,
            base64.b64encode(f"{USERNAME}:{PASSWORD}".encode()).decode(),
            TEXT,
        ]:
            self.assertNotIn(secret, serialized)

    def test_stops_if_unauthenticated_home_is_public(self):
        self.service.overrides[("GET", "/", False)] = lambda _: httpx.Response(200, text="public")
        with self.assertRaisesRegex(AssertionError, "http_status"):
            self.verification.run()
        self.assertEqual(len(self.service.requests), 1)
        self.assertNotIn("authorization", self.service.requests[0].headers)

    def test_redirect_is_not_followed_and_never_sends_auth_to_other_host(self):
        self.service.overrides[("GET", "/", True)] = lambda _: httpx.Response(
            302, headers={"Location": "https://other.example/login"}
        )
        with self.assertRaisesRegex(AssertionError, "http_status"):
            self.verification.run()
        self.assertTrue(all(r.url.host == "review.example.org" for r in self.service.requests))

    def test_does_not_request_cross_origin_or_non_asset_frontend_links(self):
        for value in [
            "https://other.example/a.js",
            "//other.example/a.js",
            "/api/documents/private/pdf",
            "/assets/a.js?token=unexpected",
        ]:
            with self.subTest(value=value), self.assertRaises(ValueError):
                asset_path(BASE, value)

    def test_requires_all_three_models_ready_without_running_inference(self):
        self.service.health["embedding_ready"] = False
        with self.assertRaisesRegex(AssertionError, "three_local_models_ready"):
            self.verification.run()
        self.assertFalse(any(r.method == "POST" for r in self.service.requests))

    def test_detects_wrong_pdf_range(self):
        def wrong_range(request):
            if request.headers.get("Range"):
                return httpx.Response(
                    206, headers={"Content-Range": "bytes 1-1024/9999"}, content=PDF[:1024]
                )
            return httpx.Response(200, content=PDF)

        self.service.overrides[("GET", PREFIX + "/pdf", True)] = wrong_range
        with self.assertRaisesRegex(AssertionError, "pdf_range"):
            self.verification.run()

    def test_detects_original_pdf_byte_changes(self):
        def modified_pdf(request):
            if request.headers.get("Range"):
                return httpx.Response(
                    206, headers={"Content-Range": f"bytes 0-1023/{len(PDF)}"}, content=PDF[:1024]
                )
            return httpx.Response(200, content=PDF[:-1] + b"!")

        self.service.overrides[("GET", PREFIX + "/pdf", True)] = modified_pdf
        with self.assertRaisesRegex(AssertionError, "original_pdf_hash"):
            self.verification.run()

    def test_detects_source_hash_or_codepoint_offset_mismatch(self):
        original = navigation()
        for field, wrong in [("start", 1), ("page_sha256", "0" * 64)]:
            self.service.links = copy.deepcopy(original)
            self.service.links["mentions"][0][field] = wrong
            with (
                self.subTest(field=field),
                self.assertRaisesRegex(AssertionError, "sampled_source_interval"),
            ):
                self.verification.run()

    def test_bounds_download_response_size(self):
        with self.assertRaisesRegex(ValueError, "response_size_or_time_limit"):
            self.verification.read("/assets/a.js", limit=3)

    def test_read_guard_rejects_mutations_and_auth_destination_changes(self):
        for path, method in [
            ("/api/documents", "POST"),
            ("https://other.example/assets/a.js", "GET"),
            ("//other.example/assets/a.js", "GET"),
            ("/api/health?unexpected=secret", "GET"),
        ]:
            with self.subTest(path=path), self.assertRaises(ValueError):
                self.verification.read(path, method=method)
        self.assertEqual(self.service.requests, [])

    def test_cli_error_report_does_not_echo_exception_or_credentials(self):
        def fail(_request):
            raise httpx.ConnectError("untrusted exception echoes " + PASSWORD)

        self.service.overrides[("GET", "/", False)] = fail
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory) / "report.json"
            arguments = [
                "verify_public_demo.py",
                "--base-url",
                BASE,
                "--document-id",
                DOCUMENT_ID,
                "--output",
                str(target),
            ]
            output = io.StringIO()
            environment = {"YANXI_VERIFY_USER": USERNAME, "YANXI_VERIFY_PASSWORD": PASSWORD}
            with (
                patch.dict(os.environ, environment),
                patch("sys.argv", arguments),
                patch("scripts.verify_public_demo.Verification", return_value=self.verification),
                contextlib.redirect_stderr(output),
            ):
                self.assertEqual(main(), 1)
            self.assertNotIn(PASSWORD, target.read_text() + output.getvalue())
            record = json.loads(target.read_text())
            self.assertFalse(record["all_requested_checks_passed"])
            self.assertEqual(record["error_type"], "ConnectError")

    def test_non_interactive_cli_does_not_fallback_to_echoing_password(self):
        arguments = [
            "verify_public_demo.py",
            "--base-url",
            BASE,
            "--document-id",
            DOCUMENT_ID,
            "--output",
            "not-created.json",
        ]
        with (
            patch.dict(os.environ, {"YANXI_VERIFY_USER": "", "YANXI_VERIFY_PASSWORD": ""}),
            patch("sys.argv", arguments),
            patch("sys.stdin.isatty", return_value=False),
            patch("builtins.input") as prompt,
            patch("getpass.getpass") as password,
            contextlib.redirect_stderr(io.StringIO()),
        ):
            self.assertEqual(main(), 1)
        prompt.assert_not_called()
        password.assert_not_called()


if __name__ == "__main__":
    unittest.main()
