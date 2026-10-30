import importlib
import tempfile
import threading
import time
import unittest
from collections import Counter
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient

from backend.core import Store
from backend.translation import (
    TranslationService,
    canonical_numbers,
    digest,
    protect,
    restore,
    segments,
    translate_segment,
)


class FakeModel:
    llm = object()
    llm_path = "/local/test-model"

    def __init__(self):
        self.calls = 0

    def translate(self, text, target):
        self.calls += 1
        return "中文译文：" + text


class TranslationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.store = Store(Path(self.temp.name))
        self.models = FakeModel()
        self.service = TranslationService(self.store, self.models)
        self.doc, _ = self.store.create(b"%PDF-test", "paper.pdf")
        self.doc_id = self.doc["id"]
        self.write_page(
            "The model uses 32 GPUs and a learning rate of 0.0003.\n\nAttention is useful."
        )
        self.store.finish(self.doc_id, 1, [], title="Test Paper")

    def write_page(self, text):
        self.store.save_page(
            self.doc_id,
            1,
            {
                "text": text,
                "method": "native",
                "blocks": [],
                "width": 100,
                "height": 100,
            },
        )

    def tearDown(self):
        self.service.shutdown()
        self.temp.cleanup()

    def wait(self, job):
        for _ in range(200):
            current = self.service.job(job["id"])
            if current["status"] not in ("queued", "running"):
                return current
            time.sleep(0.01)
        self.fail("翻译任务未完成")

    def test_protected_math_code_urls_and_numbers_round_trip(self):
        source = "Use $x_1=0.5$ and \\(a+b\\), $$y=2$$, `x=3` and 0.0003, 32, 1e-5. GPT-4, seq2seq [12]. https://example.org/a\n```python\nx=4\n```\narXiv:2309.15217v2 [cs.CL] 28 Apr 2025"
        masked, saved = protect(source)
        self.assertNotIn("0.0003", masked)
        self.assertEqual(restore(masked, saved), source)
        with self.assertRaises(ValueError):
            restore(masked.replace(next(iter(saved)), ""), saved)
        with self.assertRaises(ValueError):
            restore(masked + next(iter(saved)), saved)
        translated = translate_segment(source, "zh", self.models)
        for value in saved.values():
            self.assertIn(value, translated)

    def test_split_keeps_every_character_and_entire_code_block(self):
        code = "```python\n" + "print(1)\n" * 200 + "```"
        source = "First paragraph.\n\n" + "More text here. " * 180 + code
        parts = segments(source)
        self.assertEqual("".join(parts), source)
        self.assertTrue(any(code in part for part in parts))
        self.assertGreater(len(parts), 2)

    def test_formal_bibliography_keeps_original_names_and_publications(self):
        text = "Author One. 2022. Paper. CoRR, abs/2211.08411.\nAuthor Two. 2020. Other paper. In Proceedings of Conference."
        self.assertEqual(translate_segment(text, "zh", self.models), text)
        self.assertEqual(self.models.calls, 0)

    def test_title_author_contact_header_is_kept_before_abstract(self):
        header = "A Paper Title\nAuthor One, University\nauthor@example.org\nAbstract\n"
        result = translate_segment(header + "We use a local model.", "zh", self.models)
        self.assertTrue(result.startswith(header))
        self.assertIn("中文译文", result)

    def test_invalid_numeric_output_rejected_and_small_context_retry(self):
        with patch.object(self.models, "translate", return_value="学习率为 0.02"):
            with self.assertRaises(ValueError):
                translate_segment("Learning rate is 0.01.", "zh", self.models)
        source = "This is a scientific sentence with 32 GPUs. " * 16

        def shorter_only(text, target):
            return "译文：" + text if len(text) < 400 else "省略了所有数字"

        with patch.object(self.models, "translate", side_effect=shorter_only):
            result = translate_segment(source, "zh", self.models)
        self.assertEqual(result.count("32"), 16)

    def test_chinese_integer_spelling_restored_only_when_unambiguous(self):
        with patch.object(self.models, "translate", return_value="分为三个部分"):
            self.assertEqual(
                translate_segment("Divided into 3 sections.", "zh", self.models), "分为3个部分"
            )
        self.assertEqual(
            canonical_numbers("有三个模型和三个任务", Counter({"3": 1})), "有三个模型和三个任务"
        )

    def test_cache_reuse_and_source_change_invalidation(self):
        job = self.wait(self.service.start("zh", self.doc_id))
        self.assertEqual(job["status"], "ready")
        self.assertEqual(job["completed"], job["total"])
        page = self.service.page(self.doc_id, 1, "zh")
        self.assertTrue(page["complete"])
        self.assertIn("0.0003", page["segments"][0]["translation"])
        calls = self.models.calls
        self.assertEqual(self.wait(self.service.start("zh", self.doc_id))["status"], "ready")
        self.assertEqual(self.models.calls, calls)
        self.assertIn("物理第 1 页", self.service.export(self.doc_id, "zh"))
        self.write_page("The changed evidence uses 64 GPUs.")
        self.assertFalse(self.service.page(self.doc_id, 1, "zh")["complete"])
        with self.assertRaises(ValueError):
            self.service.export(self.doc_id, "zh")

    def test_partial_failure_keeps_verified_segments_and_retry_resumes(self):
        self.write_page("First paragraph uses 32 GPUs.\n\nSecond paragraph has 64 GPUs.")
        original = self.models.translate

        def flaky(text, target):
            if "Second" in text:
                raise ValueError("模拟模型未返回有效译文")
            return original(text, target)

        with patch.object(self.models, "translate", side_effect=flaky):
            job = self.wait(self.service.start("zh", self.doc_id))
        self.assertEqual(job["status"], "error")
        self.assertEqual(job["completed"], 1)
        page = self.service.page(self.doc_id, 1, "zh")
        self.assertIsNotNone(page["segments"][0]["translation"])
        self.assertIsNone(page["segments"][1]["translation"])
        self.assertEqual(self.wait(self.service.start("zh", self.doc_id))["status"], "ready")
        self.assertEqual(self.models.calls, 2)

    def test_cancel_deduplicate_queue_limit_and_resume(self):
        started, release = threading.Event(), threading.Event()

        def blocking(text, target):
            started.set()
            release.wait(3)
            return "译文：" + text

        try:
            with patch.object(self.models, "translate", side_effect=blocking):
                first = self.service.start("zh", self.doc_id)
                self.assertTrue(started.wait(2))
                same = self.service.start("zh", self.doc_id)
                self.assertEqual(first["id"], same["id"])
                queued = self.service.start("zh", text="Other text")
                with self.assertRaises(OverflowError):
                    self.service.start("zh", text="Third text")
                self.service.cancel(first["id"])
                self.service.cancel(queued["id"])
                release.set()
                self.assertEqual(self.wait(first)["status"], "cancelled")
                self.assertEqual(self.wait(queued)["status"], "cancelled")
        finally:
            release.set()
        self.assertEqual(self.wait(self.service.start("zh", self.doc_id))["status"], "ready")

    def test_archive_and_unresolved_versions_are_not_translated(self):
        self.store.trash(self.doc_id)
        with self.assertRaises(ValueError):
            self.service.start("zh", self.doc_id)
        self.store.trash(self.doc_id, restore=True)
        with self.store.connect() as db:
            db.execute("UPDATE documents SET status='review' WHERE id=?", (self.doc_id,))
        with self.assertRaises(ValueError):
            self.service.start("zh", self.doc_id)

    def test_restart_recovers_jobs_with_explicit_error(self):
        with self.store.connect() as db:
            db.execute(
                "INSERT INTO translation_jobs(id,pages,status) VALUES('interrupted','[]','running')"
            )
        self.service.recover()
        self.assertEqual(self.service.job("interrupted")["status"], "error")
        self.assertIn("重启", self.service.job("interrupted")["error"])

    def test_selection_verifies_unicode_offsets_and_returns_source_proof(self):
        text = "🙂 文献正文。The model uses 32 GPUs and 0.0003 learning rate."
        self.write_page(text)
        start = text.index("The")
        selected = text[start:]
        job = self.service.start_selection(self.doc_id, 1, start, len(text), selected, "zh")
        self.assertEqual(job["source"]["source_hash"], digest(text))
        self.assertEqual(job["source"]["start"], start)
        self.assertEqual(job["source"]["text"], selected)
        result = self.wait(job)
        self.assertEqual(result["status"], "ready")
        self.assertIn("0.0003", result["result"])
        # 选段翻译不将局部结果冒充整页翻译，也不写问答索引。
        self.assertFalse(self.service.page(self.doc_id, 1, "zh")["complete"])

    def test_selection_rejects_stale_forged_out_of_range_and_archived_source(self):
        with patch.object(self.service, "start") as start:
            for low, high, text in ((0, 5, "wrong"), (-1, 3, "The"), (1, 10000, "The"), (5, 1, "")):
                with self.assertRaises(ValueError):
                    self.service.start_selection(self.doc_id, 1, low, high, text, "zh")
            with self.assertRaises(KeyError):
                self.service.start_selection(self.doc_id, 2, 0, 3, "The", "zh")
            self.write_page("New page text")
            with self.assertRaises(ValueError):
                self.service.start_selection(self.doc_id, 1, 0, 3, "The", "zh")
            self.store.trash(self.doc_id)
            with self.assertRaises(ValueError):
                self.service.start_selection(self.doc_id, 1, 0, 3, "New", "zh")
            start.assert_not_called()

    def test_selection_api_limits_and_queued_worker(self):
        module = importlib.import_module("backend.app")
        with patch.object(module, "translation", self.service):
            client = TestClient(module.app)
            url = f"/api/documents/{self.doc_id}/translation/selection/1"
            data = {"text": "The model uses 32 GPUs", "start": 0, "end": 22, "target": "zh"}
            data["end"] = len(data["text"])
            response = client.post(url, json=data)
            self.assertEqual(response.status_code, 202)
            self.assertEqual(self.wait(response.json())["status"], "ready")
            self.assertEqual(client.post(url, json={**data, "end": 1}).status_code, 409)
            self.assertEqual(client.post(url, json={**data, "start": -1}).status_code, 422)
            self.assertEqual(client.post(url, json={**data, "text": "x" * 801}).status_code, 422)
            self.assertEqual(client.post(url, json={**data, "target": "fr"}).status_code, 422)

    def test_api_validation_export_and_real_worker_flow(self):
        module = importlib.import_module("backend.app")
        with (
            patch.object(module, "store", self.store),
            patch.object(module, "translation", self.service),
        ):
            client = TestClient(module.app)
            base = f"/api/documents/{self.doc_id}/translation"
            self.assertEqual(client.get(base + "/pages/2").status_code, 404)
            self.assertEqual(client.post(base, json={"page": 0}).status_code, 422)
            self.assertEqual(client.post(base, json={"target": "fr"}).status_code, 422)
            self.assertEqual(
                client.post("/api/translation/text", json={"text": " "}).status_code, 422
            )
            self.assertEqual(client.get(base + "/export").status_code, 409)
            response = client.post(base, json={"target": "zh", "page": 1})
            self.assertEqual(response.status_code, 202)
            self.wait(response.json())
            self.assertEqual(client.get(base + "/pages/1").json()["complete"], True)
            exported = client.get(base + "/export")
            self.assertEqual(exported.status_code, 200)
            self.assertIn("attachment", exported.headers["content-disposition"])
            self.assertEqual(len(client.get(base + "/jobs").json()), 1)
            snippet = client.post(
                "/api/translation/text", json={"text": "Learning rate is 0.01.", "target": "zh"}
            )
            self.assertEqual(snippet.status_code, 202)
            self.assertIn("0.01", self.wait(snippet.json())["result"])


if __name__ == "__main__":
    unittest.main()
