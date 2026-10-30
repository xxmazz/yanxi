import importlib
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient

from backend.core import Store, chunks_for_page
from backend.fulltext_check import evidence_for
from backend.graph import build_graph, sha


class FulltextTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.store = Store(Path(self.temp.name))
        self.origin = self.make(
            "Origin",
            "1706.00001",
            "We propose attention.\nFuture work will study attention evaluation.",
        )
        self.target = self.make(
            "New study",
            "2301.00001",
            "We evaluate attention with a controlled benchmark.\n\nOur study limits evaluation to English text.\n\nReferences\nOther Author. Attention evaluation paper.",
        )
        self.graph = build_graph(self.store, [self.origin])
        self.targets = build_graph(self.store, [self.target])

    def tearDown(self):
        self.temp.cleanup()

    def make(self, title, arxiv, text):
        doc, _ = self.store.create(("%PDF-" + title).encode(), title + ".pdf")
        id = doc["id"]
        self.store.begin(id, 1)
        self.store.save_page(
            id, 1, {"text": text, "method": "native", "width": 100, "height": 100, "blocks": []}
        )
        self.store.finish(
            id,
            1,
            chunks_for_page(text, 1, size=65, overlap=0),
            title=title,
            metadata={"arxiv_id": arxiv},
        )
        return id

    def test_body_scope_hash_offsets_and_reference_exclusion(self):
        report = evidence_for(self.store, self.graph, self.targets, "attention evaluation", None)
        self.assertEqual(report["after_year"], 2017)
        evidence = report["groups"][0]["evidence"]
        self.assertTrue(evidence)
        for item in evidence:
            page = self.store.page(item["document_id"], item["page"])
            self.assertEqual(page["text"][item["start"] : item["end"]], item["text"])
            self.assertEqual(item["page_sha256"], sha(page["text"]))
            self.assertNotIn("Other Author", item["text"])
        with self.assertRaises(ValueError):
            evidence_for(self.store, self.graph, self.graph, "attention", None)
        with self.assertRaises(ValueError):
            evidence_for(self.store, {**self.graph, "nodes": []}, self.targets, "attention", None)

    def test_api_real_source_claim_validation_and_changed_fingerprint(self):
        module = importlib.import_module("backend.app")
        client = TestClient(module.app)
        payload = {
            "document_ids": [self.origin],
            "target_document_ids": [self.target],
            "fingerprint": self.graph["fingerprint"],
            "topic": "attention evaluation",
        }
        with (
            patch.object(module, "store", self.store),
            patch.object(module.models, "llm", object()),
            patch.object(module.models, "encoder", object()),
            patch.object(module.models, "discovery_query", return_value="attention evaluation"),
            patch.object(module.models, "encode", return_value=[None]),
            patch.object(
                module.models,
                "answer",
                return_value={
                    "claims": [{"text": "本篇研究注意力评估。", "source": "E1"}],
                    "insufficient_evidence": False,
                },
            ) as model,
        ):
            result = client.post("/api/graph/fulltext-check", json=payload)
            self.assertEqual(result.status_code, 200, result.text)
            self.assertTrue(result.json()["groups"][0]["supported"])
            self.assertEqual(result.json()["groups"][0]["citations"][0]["document_id"], self.target)
            model.return_value = {
                "claims": [{"text": "Invented source", "source": "E99"}],
                "insufficient_evidence": False,
            }
            result = client.post("/api/graph/fulltext-check", json=payload)
            self.assertFalse(result.json()["groups"][0]["supported"])
            self.assertNotIn("Invented source", result.text)
            self.assertEqual(
                client.post(
                    "/api/graph/fulltext-check", json={**payload, "fingerprint": "a" * 64}
                ).status_code,
                409,
            )
            real = module.build_graph
            calls = 0

            def changed(*args):
                nonlocal calls
                value = real(*args)
                calls += 1
                return {**value, "fingerprint": "b" * 64} if calls >= 3 else value

            with patch.object(module, "build_graph", side_effect=changed):
                self.assertEqual(
                    client.post("/api/graph/fulltext-check", json=payload).status_code, 409
                )
