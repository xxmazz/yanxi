import importlib
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient
from pypdf import PdfWriter

from backend.core import Store


class ApiTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.module = importlib.import_module("backend.app")
        self.old = self.module.store
        self.module.store = Store(Path(self.temp.name))
        self.client = TestClient(self.module.app)

    def tearDown(self):
        self.module.store = self.old
        self.temp.cleanup()

    def wait_document(self, doc_id):
        for _ in range(100):
            doc = self.module.store.document(doc_id)
            if doc["status"] != "queued":
                return doc
            time.sleep(0.01)
        self.fail("索引任务没有完成")

    def test_reject_invalid_pdf_and_missing_source(self):
        self.assertEqual(
            self.client.post(
                "/api/documents", files={"file": ("bad.pdf", b"not a pdf")}
            ).status_code,
            400,
        )
        self.assertEqual(self.client.get("/api/documents/nonexistent/pdf").status_code, 404)

    def test_empty_library_refuses_answer(self):
        result = self.client.post("/api/chat", json={"question": "学习率？"}).json()
        self.assertTrue(result["insufficient_evidence"])
        self.assertEqual(result["citations"], [])

    def test_scanned_pdf_is_explicit_error(self):
        import io

        writer = PdfWriter()
        writer.add_blank_page(width=100, height=100)
        content = io.BytesIO()
        writer.write(content)
        response = self.client.post(
            "/api/documents", files={"file": ("scan.pdf", content.getvalue())}
        )
        self.assertEqual(response.status_code, 202)
        doc = self.wait_document(response.json()["id"])
        self.assertEqual(doc["status"], "error")
        self.assertIn("OCR", doc["error"])

    def test_scope_and_citation_endpoint(self):
        doc, _ = self.module.store.create(b"%PDF-test", "test.pdf")
        self.module.store.finish(
            doc["id"],
            3,
            [
                {
                    "page": 3,
                    "offset": 0,
                    "text": "learning rate 0.0003 with batch size 32",
                }
            ],
        )
        with (
            patch.object(self.module.models, "llm", object()),
            patch.object(self.module.models, "encode", return_value=None),
            patch.object(self.module.models, "search_query", return_value="learning rate"),
            patch.object(
                self.module.models,
                "answer",
                return_value={
                    "claims": [
                        {
                            "text": "学习率为0.0003",
                            "source": "E1",
                            "quote": "learning rate 0.0003 with batch size 32",
                        }
                    ],
                    "insufficient_evidence": False,
                },
            ),
        ):
            result = self.client.post(
                "/api/chat",
                json={"question": "learning rate", "document_ids": [doc["id"]]},
            ).json()
            self.assertEqual(result["citations"][0]["page"], 3)
            self.assertIn("#page=3", result["citations"][0]["url"])
            denied = self.client.post(
                "/api/chat",
                json={"question": "learning rate", "document_ids": ["other"]},
            ).json()
            self.assertTrue(denied["insufficient_evidence"])


if __name__ == "__main__":
    unittest.main()
