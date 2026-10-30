import importlib
import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch

from fastapi.testclient import TestClient

from backend.conversation import context_scope
from backend.core import Store


class ConversationTests(unittest.TestCase):
    def test_followup_scope_and_explicit_selection_precedence(self):
        self.assertEqual(context_scope("其中Adam的参数？", [], ["a", "a"]), (["a"], True))
        self.assertEqual(context_scope("这篇用了什么GPU？", ["b"], ["a"]), (["b"], False))
        self.assertEqual(context_scope("BERT的参数量？", [], ["a"]), ([], False))
        self.assertEqual(context_scope("其中Adam的参数？", [], []), ([], False))
        self.assertEqual(context_scope("In this paper, which GPU?", [], ["a"]), (["a"], True))

    def test_route_passes_only_source_scope_to_new_evidence_answer(self):
        module = importlib.import_module("backend.app")
        with tempfile.TemporaryDirectory() as folder:
            store = Store(Path(folder))
            doc, _ = store.create(b"%PDF-context", "context.pdf")
            store.finish(doc["id"], 1, [])
            answer = AsyncMock(
                return_value={"answer": "new evidence", "steps": [], "citations": []}
            )
            with (
                patch.object(module, "store", store),
                patch.object(module, "answer_question", answer),
            ):
                result = TestClient(module.app).post(
                    "/api/chat",
                    json={"question": "其中Adam的参数？", "previous_document_ids": [doc["id"]]},
                )
                self.assertEqual(result.status_code, 200)
                self.assertTrue(result.json()["context_used"])
                self.assertEqual(answer.call_args.args[0].document_ids, [doc["id"]])
                self.assertEqual(answer.call_args.args[0].question, "其中Adam的参数？")

    def test_missing_archived_or_replaced_context_never_falls_back_to_all(self):
        module = importlib.import_module("backend.app")
        with tempfile.TemporaryDirectory() as folder:
            store = Store(Path(folder))
            doc, _ = store.create(b"%PDF-context", "context.pdf")
            store.finish(doc["id"], 1, [])
            store.trash(doc["id"])
            with (
                patch.object(module, "store", store),
                patch.object(module, "answer_question", new_callable=AsyncMock) as answer,
            ):
                for doc_id in ["missing", doc["id"]]:
                    result = (
                        TestClient(module.app)
                        .post(
                            "/api/chat",
                            json={"question": "这篇的参数？", "previous_document_ids": [doc_id]},
                        )
                        .json()
                    )
                    self.assertTrue(result["insufficient_evidence"])
                    self.assertEqual(result["citations"], [])
                answer.assert_not_called()

    def test_context_size_is_bounded(self):
        module = importlib.import_module("backend.app")
        result = TestClient(module.app).post(
            "/api/chat", json={"question": "这篇呢", "previous_document_ids": ["a"] * 7}
        )
        self.assertEqual(result.status_code, 422)


if __name__ == "__main__":
    unittest.main()
