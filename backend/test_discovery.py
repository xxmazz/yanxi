"""官方 Atom 协议、缓存、下载边界、来源与后台导入行为。"""

import importlib
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import httpx
from fastapi.testclient import TestClient

from backend.core import Store
from backend.discovery import (
    ArxivDiscovery,
    DiscoveryError,
    github_repositories,
    parse_feed,
    query_expression,
    valid_id,
)

FEED = b"""<feed xmlns="http://www.w3.org/2005/Atom" xmlns:op="http://a9.com/-/spec/opensearch/1.1/" xmlns:arxiv="http://arxiv.org/schemas/atom"><op:totalResults>1</op:totalResults><entry><id>http://arxiv.org/abs/1706.03762v7</id><title>Attention Is All You Need</title><summary>Transformer model with attention. https://github.com/example/research.</summary><author><name>Ashish Vaswani</name></author><published>2017-06-12T00:00:00Z</published><updated>2023-08-02T00:00:00Z</updated><category term="cs.CL"/><arxiv:comment>Code available at https://github.com/example/research</arxiv:comment><link title="pdf" href="http://127.0.0.1/private"/></entry></feed>"""


class DiscoveryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.store = Store(Path(self.temp.name))

    def tearDown(self):
        self.temp.cleanup()

    def service(self, handler):
        return ArxivDiscovery(self.store, httpx.MockTransport(handler), interval=0)

    def test_atom_metadata_and_official_links(self):
        result = parse_feed(FEED)
        item = result["items"][0]
        self.assertEqual(result["total"], 1)
        self.assertEqual(item["authors"], ["Ashish Vaswani"])
        self.assertEqual(item["base_id"], "1706.03762")
        self.assertEqual(item["pdf_url"], "https://arxiv.org/pdf/1706.03762v7")
        self.assertEqual(item["code_urls"], ["https://github.com/example/research"])
        self.assertEqual(
            github_repositories("https://github.com/a/b), https://github.com.evil.test/a/b"),
            ["https://github.com/a/b"],
        )
        with self.assertRaises(DiscoveryError):
            parse_feed(FEED.replace(b"http://arxiv.org/abs/", b"http://other.test/abs/"))
        for value in (b"<html>failure</html>", b"<!DOCTYPE feed><feed/>"):
            with self.assertRaises(DiscoveryError):
                parse_feed(value)

    def test_query_and_identifier_boundaries(self):
        self.assertEqual(query_expression("1706.03762v7"), {"id_list": "1706.03762v7"})
        self.assertEqual(valid_id("hep-th/9901001v2"), "hep-th/9901001v2")
        self.assertEqual(
            query_expression("attention OR cat:private")["search_query"],
            'all:"attention" AND all:"or" AND all:"cat" AND all:"private"',
        )
        for value in ("https://127.0.0.1/test", "../1706.03762", "1706.03762v0", "1706.03762?x=y"):
            with self.assertRaises(DiscoveryError):
                valid_id(value)

    def test_cache_persistence_and_explicit_stale_fallback(self):
        calls = []

        def handler(request):
            calls.append(request)
            return httpx.Response(200, content=FEED)

        service = self.service(handler)
        first = service.search("attention")
        self.assertFalse(first["cached"])
        second = service.search("attention")
        self.assertTrue(second["cached"])
        self.assertEqual(len(calls), 1)
        with self.store.connect() as db:
            db.execute("UPDATE discovery_cache SET fetched=0")
        offline = self.service(lambda request: httpx.Response(503))
        cached = offline.search("attention")
        self.assertTrue(cached["stale"])
        self.assertIn("缓存", cached["warning"])
        with self.assertRaises(DiscoveryError):
            offline.search("new topic")

    def test_bounded_official_pdf_download(self):
        item = parse_feed(FEED)["items"][0]
        good = self.service(lambda request: httpx.Response(200, content=b"%PDF-original"))
        self.assertEqual(good.download(item), b"%PDF-original")
        for response in (
            httpx.Response(302, headers={"location": "http://127.0.0.1/private"}),
            httpx.Response(302, headers={"location": "https://example.com/paper.pdf"}),
            httpx.Response(200, content=b"<html>not PDF</html>"),
            httpx.Response(200, headers={"content-length": str(101 * 1024 * 1024)}),
        ):
            with self.subTest(response=response.status_code), self.assertRaises(DiscoveryError):
                self.service(lambda request: response).download(item)

    def test_source_preserved_and_archived_state_visible(self):
        service = self.service(lambda request: httpx.Response(200, content=FEED))
        item = service.lookup("1706.03762v7")
        doc, _ = self.store.create(b"%PDF-test", "random.pdf")
        self.store.finish(doc["id"], 1, [{"page": 1, "offset": 0, "text": "evidence"}])
        service.source(doc["id"], item)
        self.assertEqual(service.sources(doc["id"])[0]["url"], item["url"])
        self.store.trash(doc["id"])
        self.assertTrue(service.imported()["1706.03762v7"]["archived"])
        with self.assertRaises(DiscoveryError):
            service.lookup("1706.03762v1")

    def test_background_import_and_no_silent_restore(self):
        module = importlib.import_module("backend.app")
        service = self.service(
            lambda request: httpx.Response(
                200, content=FEED if "/api/query" in request.url.path else b"%PDF-original"
            )
        )
        with (
            patch.object(module, "store", self.store),
            patch.object(module, "discovery", service),
            patch.object(module.workers, "submit") as submitted,
        ):
            service.create_job("first", "1706.03762v7")
            module.pending.acquire()
            module.download_slots.acquire()
            module.import_paper_job("first", "1706.03762v7")
            job = service.job("first")
            self.assertEqual(job["status"], "complete")
            self.assertTrue(service.sources(job["document_id"]))
            submitted.assert_called_once()
            module.pending.release()  # mock 索引任务不会自行释放
            self.store.finish(job["document_id"], 1, [{"page": 1, "offset": 0, "text": "evidence"}])
            self.store.trash(job["document_id"])
            service.create_job("second", "1706.03762v7")
            module.pending.acquire()
            module.download_slots.acquire()
            module.import_paper_job("second", "1706.03762v7")
            self.assertEqual(service.job("second")["status"], "error")
            self.assertIn("归档", service.job("second")["error"])
            self.assertTrue(self.store.document(job["document_id"])["trashed_at"])

    def test_common_scientific_terms_do_not_need_unreliable_model_guess(self):
        from backend.models import LocalModels

        model = LocalModels()
        with patch.object(model, "generate", side_effect=AssertionError("should use glossary")):
            self.assertEqual(
                model.discovery_query("检索增强生成"), "retrieval augmented generation"
            )
            self.assertEqual(
                model.discovery_query("参数高效微调"), "parameter efficient fine tuning"
            )

    def test_library_ranking_and_code_source_are_visible(self):
        import numpy as np

        module = importlib.import_module("backend.app")
        service = self.service(lambda request: httpx.Response(200, content=FEED))
        doc, _ = self.store.create(b"%PDF-library", "library.pdf")
        self.store.finish(
            doc["id"], 1, [{"page": 1, "offset": 0, "text": "evidence"}], title="Related paper"
        )
        with (
            patch.object(module, "store", self.store),
            patch.object(module, "discovery", service),
            patch.object(module.models, "encoder", object()),
            patch.object(
                module.models, "encode", return_value=[np.array([1.0, 0.0]), np.array([0.8, 0.6])]
            ),
        ):
            result = (
                TestClient(module.app)
                .get("/api/discovery/search", params={"q": "attention", "sort": "library"})
                .json()
            )
            self.assertEqual(result["items"][0]["related_document"]["id"], doc["id"])
            self.assertEqual(result["items"][0]["related_document"]["similarity"], 0.8)
            self.assertEqual(
                result["items"][0]["code_urls"], ["https://github.com/example/research"]
            )

    def test_routes_and_chinese_keyword_conversion(self):
        module = importlib.import_module("backend.app")
        service = self.service(lambda request: httpx.Response(200, content=FEED))
        with (
            patch.object(module, "store", self.store),
            patch.object(module, "discovery", service),
            patch.object(module.models, "llm", object()),
            patch.object(module.models, "discovery_query", return_value="attention transformer"),
            patch.object(module.models, "encoder", None),
        ):
            client = TestClient(module.app)
            response = client.get("/api/discovery/search", params={"q": "注意力模型"})
            self.assertEqual(response.status_code, 200)
            self.assertEqual(response.json()["search_query"], "attention transformer")
            self.assertIn("将中文主题转换为英文检索词", response.json()["steps"])
            self.assertEqual(
                client.post(
                    "/api/discovery/import", json={"arxiv_id": "https://localhost/test"}
                ).status_code,
                400,
            )
            self.assertEqual(
                client.get("/api/discovery/search", params={"q": "test", "start": -1}).status_code,
                422,
            )
            self.assertEqual(client.get("/api/documents/missing/sources").status_code, 404)
            with patch.object(module.download_workers, "submit"):
                response = client.post("/api/discovery/import", json={"arxiv_id": "1706.03762v7"})
                self.assertEqual(response.status_code, 202)
                self.assertEqual(service.job(response.json()["id"])["status"], "queued")
                module.pending.release()
                module.download_slots.release()
            service.recover_jobs()
            self.assertEqual(service.jobs()[0]["status"], "error")


if __name__ == "__main__":
    unittest.main()
