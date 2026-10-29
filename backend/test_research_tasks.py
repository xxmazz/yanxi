import tempfile
import unittest
from pathlib import Path

from backend.core import Store
from backend.research_tasks import ResearchTasks


class TaskTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.store = Store(Path(self.temp.name))
        self.tasks = ResearchTasks(self.store)
        doc, _ = self.store.create(b"%PDF-task", "paper.pdf")
        self.store.finish(
            doc["id"],
            1,
            [],
            title="A real stored document",
            metadata={"abstract": "Original abstract."},
        )
        self.doc_id = doc["id"]
        self.search_count = self.submit_count = 0

    async def asyncTearDown(self):
        await self.tasks.shutdown()
        self.temp.cleanup()

    async def search(self, _query):
        self.search_count += 1
        return {
            "items": [
                {
                    "arxiv_id": "2005.11401v4",
                    "base_id": "2005.11401",
                    "title": "Paper",
                    "url": "https://arxiv.org/abs/2005.11401v4",
                    "imported": None,
                }
            ]
        }

    async def submit(self, _id):
        self.submit_count += 1
        return {"id": "job"}

    def job(self, _id):
        return {"id": "job", "status": "complete", "document_id": self.doc_id}

    async def summarize(self, ids):
        self.assertEqual(ids, [self.doc_id])
        return {
            "answer": "Supported summary",
            "citations": [{"document_id": self.doc_id}],
            "covered_documents": ids,
            "insufficient_evidence": False,
        }

    async def run_task(self, task_id, **overrides):
        callbacks = dict(
            search=self.search,
            submit=self.submit,
            job=self.job,
            summarize=self.summarize,
            interval=0.001,
        )
        callbacks.update(overrides)
        await self.tasks.run(task_id, **callbacks)
        return self.tasks.get(task_id)

    async def test_end_to_end_and_persistent_checkpoint(self):
        task = self.tasks.create("topic", 2)
        with self.assertRaises(ValueError):
            self.tasks.create("another", 1)
        result = await self.run_task(task["id"])
        self.assertEqual(result["status"], "complete")
        self.assertEqual(result["results"][0]["document_id"], self.doc_id)
        self.assertEqual(ResearchTasks(self.store).get(task["id"]), result)
        with self.assertRaises(ValueError):
            self.tasks.retry(task["id"])

    async def test_failure_retry_reuses_imported_documents(self):
        async def fail(_ids):
            raise ValueError("model busy")

        task = self.tasks.create("topic", 1)
        result = await self.run_task(task["id"], summarize=fail)
        self.assertEqual(result["status"], "error")
        self.tasks.retry(task["id"])
        result = await self.run_task(task["id"])
        self.assertEqual(result["status"], "complete")
        self.assertEqual((self.search_count, self.submit_count), (1, 1))
        self.assertEqual(result["attempt"], 2)

    async def test_rejected_summary_retries_without_repeating_download(self):
        async def refused(_ids):
            return {"answer": "model refused", "citations": [], "insufficient_evidence": True}

        task = self.tasks.create("topic", 1)
        result = await self.run_task(task["id"], summarize=refused)
        self.assertEqual(result["status"], "needs_summary")
        self.assertEqual(len(result["results"]), 1)
        self.tasks.retry(task["id"])
        result = await self.run_task(task["id"])
        self.assertEqual(result["status"], "complete")
        self.assertEqual((self.search_count, self.submit_count), (1, 1))
        # 升级前的来源完成记录仍可补生成摘要。
        result["summary"] = await refused([])
        self.tasks.save(result)
        self.assertEqual(self.tasks.retry(task["id"])["status"], "queued")

    async def test_partial_or_foreign_summary_scope_is_not_presented_as_complete(self):
        for citations, covered in [
            ([], [self.doc_id]),
            ([self.doc_id], []),
            (["foreign"], [self.doc_id]),
        ]:

            async def incomplete(_ids):
                return {
                    "answer": "partial claim must not leak",
                    "citations": [{"document_id": key} for key in citations],
                    "covered_documents": covered,
                    "insufficient_evidence": False,
                }

            task = self.tasks.create("topic", 1)
            result = await self.run_task(task["id"], summarize=incomplete)
            self.assertEqual(result["status"], "needs_summary")
            self.assertNotIn("partial claim", result["summary"]["answer"])
            self.assertEqual(result["summary"]["citations"], [])

    async def test_topic_ranking_provenance_survives_checkpoint(self):
        async def ranked(query):
            response = await self.search(query)
            response.update(
                search_query="translated topic",
                selection_method="e5_topic",
                steps=["local topic order"],
            )
            response["items"][0].update(
                official_rank=4, topic_similarity=0.8, selection_reason="local query ranking"
            )
            return response

        task = self.tasks.create("中文研究主题", 1)
        result = await self.run_task(task["id"], search=ranked)
        self.assertEqual(result["selection_method"], "e5_topic")
        self.assertEqual(result["selected"][0]["official_rank"], 4)
        self.assertEqual(result["selected"][0]["reason"], "local query ranking")
        self.assertEqual(result["candidates"][0]["topic_similarity"], 0.8)
        self.assertEqual(result["search_steps"], ["local topic order"])

    async def test_review_wait_and_resume(self):
        with self.store.connect() as db:
            db.execute("UPDATE documents SET status='review'")
        task = self.tasks.create("topic", 1)
        result = await self.run_task(task["id"])
        self.assertEqual(result["status"], "needs_review")
        with self.store.connect() as db:
            db.execute("UPDATE documents SET status='ready'")
        self.tasks.retry(task["id"])
        self.assertEqual((await self.run_task(task["id"]))["status"], "complete")
        self.assertEqual(self.submit_count, 1)

    async def test_cancel_stops_before_submit(self):
        task = self.tasks.create("topic", 1)

        async def cancel_search(query):
            self.tasks.cancel(task["id"])
            return await self.search(query)

        result = await self.run_task(task["id"], search=cancel_search)
        self.assertEqual(result["status"], "cancelled")
        self.assertEqual(self.submit_count, 0)

    async def test_stale_result_and_restart_are_explicit(self):
        task = self.tasks.create("topic", 1)

        async def stale(_query):
            return {"stale": True}

        result = await self.run_task(task["id"], search=stale)
        self.assertEqual(result["status"], "error")
        self.tasks.retry(task["id"])
        ResearchTasks(self.store).recover()
        self.assertEqual(self.tasks.get(task["id"])["status"], "interrupted")

    async def test_timeout_and_archive_do_not_become_success(self):
        task = self.tasks.create("topic", 1)
        result = await self.run_task(task["id"], timeout=-1)
        self.assertEqual(result["status"], "error")
        self.tasks.retry(task["id"])
        with self.store.connect() as db:
            db.execute("UPDATE documents SET is_active=0")
        self.assertEqual((await self.run_task(task["id"]))["status"], "error")

    async def test_summary_archive_race_is_rejected(self):
        async def archive(_ids):
            with self.store.connect() as db:
                db.execute("UPDATE documents SET is_active=0")
            return {"answer": "stale"}

        task = self.tasks.create("topic", 1)
        result = await self.run_task(task["id"], summarize=archive)
        self.assertEqual(result["status"], "error")
        self.assertIsNone(result["summary"])

    async def test_selection_deduplicates_and_skips_archived_candidates(self):
        async def candidates(query):
            base = await self.search(query)
            item = base["items"][0]
            base["items"] = [
                {**item, "imported": {"archived": True}},
                item,
                {**item, "arxiv_id": "2005.11401v3"},
            ]
            return base

        task = self.tasks.create("topic", 3)
        result = await self.run_task(task["id"], search=candidates)
        self.assertEqual(result["status"], "complete")
        self.assertEqual(len(result["selected"]), 1)

    async def test_api_validation_missing_routes_and_queue(self):
        import importlib
        from unittest.mock import patch

        from fastapi.testclient import TestClient

        module = importlib.import_module("backend.app")
        with (
            patch.object(module, "research_tasks", self.tasks),
            patch.object(module, "start_research"),
        ):
            client = TestClient(module.app)
            self.assertEqual(client.get("/api/research/tasks/missing").status_code, 404)
            self.assertEqual(
                client.post("/api/research/tasks", json={"query": "  "}).status_code, 422
            )
            self.assertEqual(
                client.post("/api/research/tasks", json={"query": "topic", "limit": 4}).status_code,
                422,
            )
            result = client.post("/api/research/tasks", json={"query": "topic", "limit": 1})
            self.assertEqual(result.status_code, 202)
            task_id = result.json()["id"]
            self.assertEqual(client.get(f"/api/research/tasks/{task_id}").status_code, 200)
            self.assertEqual(
                client.post("/api/research/tasks", json={"query": "topic"}).status_code, 409
            )
            self.assertEqual(
                client.post(f"/api/research/tasks/{task_id}/cancel").json()["status"], "cancelled"
            )
