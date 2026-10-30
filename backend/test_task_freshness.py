import importlib
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient

from backend.core import Store
from backend.graph import build_graph, source_fingerprint
from backend.research_tasks import ResearchTasks


class TaskFreshnessTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.store = Store(Path(self.temp.name))
        self.tasks = ResearchTasks(self.store)
        doc, _ = self.store.create(b"%PDF-freshness", "sample.pdf")
        self.doc_id = doc["id"]
        self.page = {
            "text": "Abstract\nA stored source.\n1 Introduction\nContent.",
            "method": "native",
            "width": 600,
            "height": 800,
            "blocks": [],
            "warning": "",
        }
        self.store.save_page(self.doc_id, 1, self.page)
        self.store.finish(self.doc_id, 1, [], title="Source snapshot example")
        self.task = self.tasks.create("source topic", 1)
        self.task.update(
            status="complete",
            selected=[{"document_id": self.doc_id}],
            results=[{"document_id": self.doc_id}],
            summary={
                "answer": "Old supported answer",
                "citations": [{"document_id": self.doc_id}],
                "insufficient_evidence": False,
                "fingerprint": source_fingerprint(self.store, [self.doc_id]),
            },
        )
        self.tasks.save(self.task)

    def tearDown(self):
        self.temp.cleanup()

    def test_current_summary_and_graph_use_identical_fingerprint(self):
        self.assertEqual(
            source_fingerprint(self.store, [self.doc_id]),
            build_graph(self.store, [self.doc_id])["fingerprint"],
        )
        self.assertEqual(self.tasks.view(self.task)["status"], "complete")
        with self.assertRaises(ValueError):
            self.tasks.retry(self.task["id"])

    def test_changed_page_hides_old_answer_without_rewriting_history(self):
        self.store.save_page(self.doc_id, 1, {**self.page, "text": "Changed source text"})
        view = self.tasks.view(self.tasks.get(self.task["id"]))
        self.assertEqual(view["status"], "needs_summary")
        self.assertTrue(view["summary_stale"])
        self.assertEqual(view["summary"]["citations"], [])
        self.assertNotIn("Old supported answer", view["summary"]["answer"])
        self.assertEqual(
            self.tasks.get(self.task["id"])["summary"]["answer"], "Old supported answer"
        )

    def test_archive_and_legacy_missing_summary_are_retryable_views(self):
        self.store.trash(self.doc_id)
        self.assertEqual(self.tasks.view(self.task)["status"], "needs_summary")
        self.task["summary"] = None
        self.tasks.save(self.task)
        self.assertEqual(self.tasks.view(self.task)["status"], "needs_summary")
        result = self.tasks.retry(self.task["id"])
        self.assertEqual(result["status"], "queued")
        self.assertEqual(result["selected"], self.task["selected"])

    def test_api_only_retries_complete_task_when_its_snapshot_is_stale(self):
        module = importlib.import_module("backend.app")
        with (
            patch.object(module, "research_tasks", self.tasks),
            patch.object(module, "start_research") as start,
        ):
            client = TestClient(module.app)
            path = f"/api/research/tasks/{self.task['id']}"
            self.assertEqual(client.post(path + "/retry").status_code, 409)
            self.store.save_page(self.doc_id, 1, {**self.page, "text": "Changed source"})
            self.assertEqual(client.get(path).json()["status"], "needs_summary")
            self.assertEqual(client.get("/api/research/tasks").json()[0]["status"], "needs_summary")
            response = client.post(path + "/retry")
            self.assertEqual(response.status_code, 202)
            self.assertIsNone(response.json()["summary"])
            self.assertEqual(response.json()["selected"], self.task["selected"])
            start.assert_called_once_with(self.task["id"])

    def test_same_document_scope_is_fingerprinted_once_per_list(self):
        cache = {}
        with patch(
            "backend.research_tasks.source_fingerprint", wraps=source_fingerprint
        ) as fingerprint:
            self.tasks.view(self.task, cache)
            self.tasks.view({**self.task, "id": "another"}, cache)
            self.assertEqual(fingerprint.call_count, 1)


if __name__ == "__main__":
    unittest.main()
