import importlib
import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch

from fastapi.testclient import TestClient

from backend.core import Store
from backend.future import prepare


def graph():
    return {
        "fingerprint": "a" * 64,
        "nodes": [
            {
                "title": "Original",
                "year": 2023,
                "future_work": [
                    {
                        "document_id": "doc",
                        "page": 5,
                        "quote": "Future work is factual source.",
                        "url": "/doc#page=5",
                    }
                ],
            }
        ],
    }


def search():
    return {
        "items": [
            {
                "base_id": "2401.00001",
                "arxiv_id": "2401.00001v1",
                "title": "New research",
                "published": "2024-01-01",
                "url": "https://arxiv.org/abs/2401.00001v1",
                "abstract": "This study evaluates retrieval augmented generation using annotated evidence.",
            },
            {
                "base_id": "2301.00001",
                "arxiv_id": "2301.00001v1",
                "title": "Older research",
                "published": "2023-01-01",
                "url": "https://arxiv.org/abs/2301.00001v1",
                "abstract": "Old relevant work that must not be called newer research.",
            },
        ],
        "search_query": "retrieval evaluation",
        "cached": False,
    }


class FutureTests(unittest.TestCase):
    def test_year_filter_dedup_and_no_original_direction_do_not_invent_results(self):
        results = search()
        results["items"].append(results["items"][0])
        report = prepare(graph(), results)
        self.assertEqual(report["after_year"], 2023)
        self.assertEqual(len(report["candidates"]), 1)
        self.assertEqual(report["candidates"][0]["text"], results["items"][0]["abstract"])
        self.assertEqual(report["origins"][0]["page"], 5)
        with self.assertRaises(ValueError):
            prepare(graph(), {**results, "stale": True})
        with self.assertRaises(ValueError):
            prepare({"fingerprint": "a" * 64, "nodes": []}, results)
        self.assertEqual(prepare(graph(), {"items": []})["candidates"], [])

    def test_api_rejects_changed_scope_and_unsupported_model_claims(self):
        module = importlib.import_module("backend.app")
        client = TestClient(module.app)
        request = {
            "document_ids": ["doc"],
            "fingerprint": "a" * 64,
            "topic": "retrieval evaluation",
        }
        with (
            tempfile.TemporaryDirectory() as temp,
            patch.object(module, "store", Store(Path(temp))),
            patch.object(module, "build_graph", return_value=graph()),
            patch.object(module, "search_papers", new=AsyncMock(return_value=search())) as query,
            patch.object(module.models, "llm", object()),
            patch.object(
                module.models,
                "answer",
                return_value={"claims": [{"source": "E99", "text": "Invented source"}]},
            ) as model,
        ):
            changed = client.post(
                "/api/graph/future-check", json={**request, "fingerprint": "b" * 64}
            )
            self.assertEqual(changed.status_code, 409)
            query.assert_not_called()
            result = client.post("/api/graph/future-check", json=request)
            self.assertEqual(result.status_code, 200)
            self.assertFalse(result.json()["comparisons"][0]["supported"])
            self.assertNotIn("Invented source", result.text)
            query.assert_awaited_once_with(q=request["topic"], start=0, sort="newest")
            with patch.object(
                module, "build_graph", side_effect=[graph(), {**graph(), "fingerprint": "c" * 64}]
            ):
                model.return_value = {
                    "claims": [{"source": "E1", "text": "本文研究检索增强生成的评估方法。"}],
                    "insufficient_evidence": False,
                }
                self.assertEqual(
                    client.post("/api/graph/future-check", json=request).status_code, 409
                )
