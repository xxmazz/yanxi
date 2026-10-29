"""全站评审入口、文件保护、跨站写入与配置失败行为；不用真实凭据。"""

import base64
import importlib
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient
from starlette.applications import Starlette
from starlette.responses import JSONResponse
from starlette.routing import Route, WebSocketRoute

from backend.access import AccessSettings, ReviewAccess, origin, password_hash


class AccessTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.password = "isolated-test-password"
        cls.encoded = password_hash(cls.password)
        cls.environment = {
            "YANXI_AUTH_USER": "reviewer",
            "YANXI_AUTH_PASSWORD_HASH": cls.encoded,
            "YANXI_AUTH_ORIGIN": "https://review.example",
        }

    def setUp(self):
        with patch.dict(os.environ, self.environment):
            self.settings = AccessSettings.from_env()
        self.called = []

        async def route(request):
            self.called.append(request.url.path)
            return JSONResponse({"protected": True})

        async def websocket(socket):
            self.fail("WebSocket should never reach application")

        app = Starlette(
            routes=[
                Route("/{path:path}", route, methods=["GET", "HEAD", "POST", "DELETE", "OPTIONS"]),
                WebSocketRoute("/ws", websocket),
            ]
        )
        self.client = TestClient(
            ReviewAccess(app, self.settings), base_url="https://review.example"
        )

    def auth(self, user="reviewer", password=None):
        value = base64.b64encode(
            f"{user}:{self.password if password is None else password}".encode()
        ).decode()
        return {"Authorization": "Basic " + value}

    def test_every_route_is_protected_before_application_runs(self):
        for path in [
            "/",
            "/assets/app.js",
            "/docs",
            "/openapi.json",
            "/api/health",
            "/api/documents/a/pdf",
            "/api/documents/a/render/1",
            "/api/research/tasks",
        ]:
            for method in ["GET", "HEAD", "POST"]:
                with self.subTest(path=path, method=method):
                    r = self.client.request(method, path)
                    self.assertEqual(r.status_code, 401)
                    self.assertIn("Basic", r.headers["WWW-Authenticate"])
                    self.assertEqual(r.headers["Cache-Control"], "no-store")
        self.assertEqual(self.called, [])

    def test_valid_account_gets_page_and_file_with_private_response_headers(self):
        for path in ["/", "/api/health", "/api/documents/a/pdf", "/docs"]:
            r = self.client.get(path, headers=self.auth())
            self.assertEqual(r.status_code, 200)
            self.assertEqual(r.headers["Cache-Control"], "no-store")
            self.assertEqual(r.headers["X-Frame-Options"], "DENY")
            self.assertEqual(r.headers["Cross-Origin-Resource-Policy"], "same-origin")
            self.assertIn("Authorization", r.headers["Vary"])

    def test_wrong_malformed_and_duplicate_credentials_never_reach_application(self):
        for value in [
            "Bearer secret",
            "Basic ???",
            "Basic " + "X" * 2050,
            "Basic " + base64.b64encode(b"no-colon").decode(),
        ]:
            self.assertEqual(
                self.client.get("/api/documents", headers={"Authorization": value}).status_code, 401
            )
        for headers in [
            self.auth(password="wrong"),
            self.auth(user="someone"),
            [("Authorization", self.auth()["Authorization"])] * 2,
        ]:
            self.assertEqual(self.client.get("/api/documents", headers=headers).status_code, 401)
        self.assertEqual(self.called, [])

    def test_write_requires_explicit_marker_even_without_browser_headers(self):
        self.assertEqual(self.client.post("/api/documents", headers=self.auth()).status_code, 403)
        headers = {**self.auth(), "X-Yanxi-Request": "1"}
        self.assertEqual(self.client.post("/api/documents", headers=headers).status_code, 200)
        headers.update(Origin="https://review.example", **{"Sec-Fetch-Site": "same-origin"})
        self.assertEqual(self.client.post("/api/documents", headers=headers).status_code, 200)

    def test_cross_origin_and_cross_site_requests_are_refused(self):
        headers = {**self.auth(), "X-Yanxi-Request": "1"}
        for bad in ["https://attacker.example", "https://review.example.attacker.example", "null"]:
            self.assertEqual(
                self.client.post("/api/documents", headers={**headers, "Origin": bad}).status_code,
                403,
            )
        for site in ["cross-site", "same-site"]:
            self.assertEqual(
                self.client.post(
                    "/api/documents", headers={**headers, "Sec-Fetch-Site": site}
                ).status_code,
                403,
            )
            self.assertEqual(
                self.client.get(
                    "/api/discovery/search",
                    headers={
                        **headers,
                        "Sec-Fetch-Site": site,
                        "Sec-Fetch-Mode": "no-cors",
                        "Sec-Fetch-Dest": "image",
                    },
                ).status_code,
                403,
            )
        self.assertEqual(
            self.client.get("/api/discovery/search", headers=self.auth()).status_code, 403
        )
        self.assertEqual(self.client.get("/api/discovery/search", headers=headers).status_code, 200)
        self.assertEqual(
            self.client.get(
                "/",
                headers={
                    **headers,
                    "Sec-Fetch-Site": "cross-site",
                    "Sec-Fetch-Mode": "navigate",
                    "Sec-Fetch-Dest": "document",
                },
            ).status_code,
            200,
        )

    def test_spoofed_host_and_forwarded_headers_do_not_change_trusted_origin(self):
        headers = {**self.auth(), "X-Yanxi-Request": "1"}
        self.assertEqual(
            self.client.get(
                "/api/documents",
                headers={
                    **headers,
                    "Host": "attacker.example",
                    "X-Forwarded-Host": "review.example",
                },
            ).status_code,
            400,
        )
        self.assertEqual(
            self.client.post(
                "/api/documents",
                headers={
                    **headers,
                    "Origin": "https://attacker.example",
                    "X-Forwarded-Host": "attacker.example",
                    "X-Forwarded-Proto": "https",
                },
            ).status_code,
            403,
        )

    def test_https_config_never_challenges_or_accepts_plain_http_credentials(self):
        plain = TestClient(self.client.app, base_url="http://review.example")
        for headers in [{}, self.auth(), {**self.auth(), "X-Forwarded-Proto": "https"}]:
            result = plain.get("/api/documents", headers=headers)
            self.assertEqual(result.status_code, 426)
            self.assertNotIn("WWW-Authenticate", result.headers)
        self.assertEqual(self.called, [])

    def test_partial_invalid_config_fails_closed_and_loopback_http_is_explicit(self):
        keys = {key: "" for key in self.environment}
        with patch.dict(os.environ, keys):
            self.assertIsNone(AccessSettings.from_env())
        for update in [
            {"YANXI_AUTH_USER": ""},
            {"YANXI_AUTH_PASSWORD_HASH": "plain-password"},
            {"YANXI_AUTH_PASSWORD_HASH": self.encoded.replace("32768", "1073741824")},
            {"YANXI_AUTH_ORIGIN": "http://review.example"},
            {"YANXI_AUTH_ORIGIN": "https://review.example/path"},
            {"YANXI_AUTH_USER": "bad:name"},
        ]:
            with (
                self.subTest(update=update),
                patch.dict(os.environ, {**self.environment, **update}),
                self.assertRaises(ValueError),
            ):
                AccessSettings.from_env()
        self.assertEqual(origin("http://127.0.0.1:18475/"), "http://127.0.0.1:18475")
        self.assertEqual(origin("https://REVIEW.example:443"), "https://review.example")

    def test_password_hash_uses_unique_salt_and_has_no_plaintext(self):
        another = password_hash(self.password)
        self.assertNotEqual(another, self.encoded)
        self.assertNotIn(self.password, another)
        self.assertNotIn(self.password, repr(self.settings))
        with self.assertRaises(ValueError):
            password_hash("short")

    def test_websocket_is_denied(self):
        from starlette.websockets import WebSocketDisconnect

        with self.assertRaises(WebSocketDisconnect) as result:
            with self.client.websocket_connect("/ws"):
                pass
        self.assertEqual(result.exception.code, 1008)

    def test_real_app_api_and_pdf_routes_are_inside_access_boundary(self):
        module = importlib.import_module("backend.app")
        client = TestClient(
            ReviewAccess(module.app, self.settings), base_url="https://review.example"
        )
        self.assertEqual(client.get("/api/health").status_code, 401)
        self.assertEqual(client.get("/api/documents/private/pdf").status_code, 401)
        self.assertEqual(client.get("/api/health", headers=self.auth()).status_code, 200)
        self.assertEqual(
            client.get("/api/documents/missing/pdf", headers=self.auth()).status_code, 404
        )

    def test_authenticated_file_preserves_range_and_head_behavior(self):
        from backend.core import Store

        module = importlib.import_module("backend.app")
        with tempfile.TemporaryDirectory() as directory:
            store = Store(Path(directory))
            content = b"%PDF-raw-source-with-original-bytes"
            doc, _ = store.create(content, "original.pdf")
            client = TestClient(
                ReviewAccess(module.app, self.settings), base_url="https://review.example"
            )
            url = f"/api/documents/{doc['id']}/pdf"
            with patch.object(module, "store", store):
                self.assertEqual(client.get(url, headers={"Range": "bytes=0-7"}).status_code, 401)
                ranged = client.get(url, headers={**self.auth(), "Range": "bytes=0-7"})
                self.assertEqual(ranged.status_code, 206)
                self.assertEqual(ranged.content, content[:8])
                self.assertEqual(ranged.headers["Cache-Control"], "no-store")
                head = client.head(url, headers=self.auth())
                self.assertEqual(head.status_code, 200)
                self.assertEqual(head.content, b"")
                self.assertEqual(int(head.headers["content-length"]), len(content))
                self.assertEqual(client.get(url, headers=self.auth()).content, content)
