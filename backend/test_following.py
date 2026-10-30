import asyncio
import importlib
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch

from fastapi.testclient import TestClient

from backend.core import Store
from backend.following import Following, reference_candidates


def navigation():
    return {
        "fingerprint": "f" * 64,
        "entries": [
            {"label": "[1]", "evidence": [{"page": 2, "quote": "Paper arXiv:1706.03762v7"}]},
            {
                "label": "[2]",
                "evidence": [{"page": 3, "quote": "duplicate https://arxiv.org/abs/1706.03762v1"}],
            },
            {"label": "[3]", "evidence": [{"page": 3, "quote": "Book without identifier"}]},
        ],
    }


class FollowingTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.store = Store(Path(self.temp.name))
        self.service = Following(self.store)

    async def asyncTearDown(self):
        await self.service.shutdown()
        self.temp.cleanup()

    def test_references_only_explicit_identifiers_deduplicate_versions_and_keep_pages(self):
        result = reference_candidates(navigation())
        self.assertEqual(len(result["items"]), 1)
        self.assertEqual(result["unresolved"], 1)
        self.assertEqual(result["items"][0]["pages"], [2])
        self.assertEqual(result["items"][0]["arxiv_id"], "1706.03762v7")

    async def test_subscription_newness_feedback_persistence_and_stale_failure(self):
        sub = self.service.subscribe("attention", "daily")
        response = {
            "items": [
                {
                    "base_id": "1706.03762",
                    "arxiv_id": "1706.03762v7",
                    "related_document": {"title": "Source", "similarity": 0.8},
                }
            ]
        }
        first = await self.service.refresh(sub["id"], AsyncMock(return_value=response))
        self.assertTrue(first["items"][0]["new_in_feed"])
        self.assertIn("Source", first["items"][0]["reason"])
        self.service.feedback(sub["id"], "1706.03762", 1)
        second = await self.service.refresh(sub["id"], AsyncMock(return_value=response))
        self.assertFalse(second["items"][0]["new_in_feed"])
        self.assertEqual(second["feedback"]["1706.03762"], 1)
        failed = await self.service.refresh(sub["id"], AsyncMock(return_value={"stale": True}))
        self.assertEqual(failed["last_run"], second["last_run"])
        self.assertEqual(failed["items"], second["items"])
        self.assertTrue(failed["error"])
        self.assertEqual(Following(self.store).get(sub["id"])["feedback"], {"1706.03762": 1})
        with self.assertRaises(ValueError):
            self.service.feedback(sub["id"], "not-in-feed", -1)

    async def test_scheduler_only_due_enabled_and_daily_weekly_intervals(self):
        daily = self.service.subscribe("attention", "daily")
        weekly = self.service.subscribe("transformer", "weekly")
        paused = self.service.subscribe("paused", "daily")
        self.service.configure(paused["id"], False)
        search = AsyncMock(return_value={"items": []})
        task = asyncio.create_task(self.service.scheduler(search))
        await asyncio.sleep(0.02)
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)
        self.assertEqual(search.await_count, 2)
        self.assertGreater(self.service.get(daily["id"])["next_run"], time.time() + 86000)
        self.assertGreater(self.service.get(weekly["id"])["next_run"], time.time() + 600000)
        self.assertIsNone(self.service.get(paused["id"])["last_run"])

    async def test_batch_partial_failure_and_retry_never_redownload_success(self):
        batch = self.service.batch(
            "source", [{"arxiv_id": "1706.03762"}, {"arxiv_id": "1607.06450"}]
        )
        calls = []
        states = {}

        async def submit(identifier):
            calls.append(identifier)
            key = str(len(calls))
            states[key] = {
                "id": key,
                "status": "error" if len(calls) == 2 else "complete",
                "error": "network",
                "document_id": identifier,
            }
            return states[key]

        self.service.start_batch(batch["id"], submit, states.get)
        await asyncio.gather(*list(self.service.running.values()))
        self.assertEqual(self.service.get(batch["id"])["status"], "partial")
        self.service.start_batch(batch["id"], submit, states.get)
        await asyncio.gather(*list(self.service.running.values()))
        self.assertEqual(calls, ["1706.03762", "1607.06450", "1607.06450"])
        self.assertEqual(self.service.get(batch["id"])["status"], "complete")

    async def test_cancel_retains_submitted_job_and_resume_reuses_it(self):
        batch = self.service.batch("source", [{"arxiv_id": "1706.03762"}])
        submit = AsyncMock(return_value={"id": "job", "status": "downloading"})
        state = {"id": "job", "status": "downloading"}
        self.service.start_batch(batch["id"], submit, lambda _: state)
        await asyncio.sleep(0.01)
        self.service.cancel(batch["id"])
        await self.service.shutdown()
        self.assertEqual(self.service.get(batch["id"])["status"], "cancelled")
        self.service.running.clear()
        state.update(status="complete", document_id="doc")
        self.service.start_batch(batch["id"], submit, lambda _: state)
        await asyncio.gather(*list(self.service.running.values()))
        self.assertEqual(submit.await_count, 1)
        self.assertEqual(self.service.get(batch["id"])["status"], "complete")

    def test_recovery_and_batch_exclusivity(self):
        batch = self.service.batch("source", [{"arxiv_id": "1706.03762"}])
        with self.assertRaises(ValueError):
            self.service.batch("source", [{"arxiv_id": "1607.06450"}])
        batch["status"] = "running"
        self.service.save(batch)
        self.service.recover()
        self.assertEqual(self.service.get(batch["id"])["status"], "interrupted")

    async def test_routes_reject_invented_ids_and_stale_source_before_submit(self):
        module = importlib.import_module("backend.app")
        with (
            patch.object(module, "following", self.service),
            patch.object(module, "navigation", return_value=navigation()),
            patch.object(module, "task_import", new_callable=AsyncMock) as submit,
        ):
            client = TestClient(module.app)
            path = "/api/documents/source/reference-batches"
            self.assertEqual(
                client.post(
                    path, json={"fingerprint": "0" * 64, "arxiv_ids": ["1706.03762v7"]}
                ).status_code,
                409,
            )
            self.assertEqual(
                client.post(
                    path, json={"fingerprint": "f" * 64, "arxiv_ids": ["1234.56789"]}
                ).status_code,
                409,
            )
            self.assertEqual(
                client.post(path, json={"fingerprint": "f" * 64, "arxiv_ids": []}).status_code, 422
            )
            submit.assert_not_called()
            sub = client.post(
                "/api/following/subscriptions", json={"query": "attention", "cadence": "weekly"}
            )
            self.assertEqual(sub.status_code, 201)
            self.assertEqual(
                client.post(
                    f"/api/following/{sub.json()['id']}/enabled", json={"enabled": False}
                ).status_code,
                200,
            )
