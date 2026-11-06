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
        self.old_discovery = self.module.discovery
        self.module.store = Store(Path(self.temp.name))
        from backend.discovery import ArxivDiscovery

        self.module.discovery = ArxivDiscovery(self.module.store)
        self.client = TestClient(self.module.app)

    def tearDown(self):
        self.module.store = self.old
        self.module.discovery = self.old_discovery
        self.temp.cleanup()

    def wait_document(self, doc_id):
        for _ in range(100):
            doc = self.module.store.document(doc_id)
            if doc["status"] not in ("queued", "processing"):
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

    def test_document_management_routes_and_invalid_decisions(self):
        doc, _ = self.module.store.create(b"%PDF-test", "test.pdf")
        self.module.store.finish(
            doc["id"], 1, [{"page": 1, "offset": 0, "text": "original evidence"}]
        )
        base = "/api/documents/" + doc["id"]
        self.assertEqual(self.client.get(base + "/pages/99").status_code, 404)
        self.assertEqual(self.client.get(base + "/render/0").status_code, 404)
        self.assertEqual(self.client.get(base + "/render/99").status_code, 404)
        self.assertEqual(
            self.client.post(
                base + "/resolve", json={"existing_id": "other", "action": "delete_all"}
            ).status_code,
            422,
        )
        self.assertEqual(
            self.client.post(
                base + "/resolve", json={"existing_id": "missing", "action": "keep_both"}
            ).status_code,
            404,
        )
        self.assertEqual(self.client.post(base + "/archive").status_code, 200)
        self.assertEqual(self.client.get("/api/documents").json(), [])
        self.assertEqual(len(self.client.get("/api/documents?include_archived=true").json()), 1)
        self.assertEqual(self.client.get(base + "/pdf").status_code, 200)
        self.assertEqual(self.client.post(base + "/restore").status_code, 200)
        self.assertEqual(len(self.client.get("/api/documents").json()), 1)
        with patch.object(self.module.workers, "submit"):
            self.assertEqual(self.client.post(base + "/reindex").status_code, 202)
            self.assertEqual(self.client.post(base + "/reindex").status_code, 409)
            self.module.pending.release()  # mock 不会执行 worker 的 finally
        self.assertEqual(self.client.post(base + "/archive").status_code, 409)

    def test_archive_clear_route_preserves_active_documents_and_requires_ids(self):
        active, archived = [self.module.store.create(f"%PDF-{i}".encode(), f"{i}.pdf")[0] for i in range(2)]
        for doc in (active, archived):
            self.module.store.finish(doc["id"], 1, [])
        self.module.store.trash(archived["id"])
        url = "/api/documents/archive/clear"
        self.assertEqual(self.client.post(url, json={"document_ids": []}).status_code, 422)
        self.assertEqual(self.client.post(url, json={"document_ids": [active["id"], archived["id"]]}).status_code, 409)
        response = self.client.post(url, json={"document_ids": [archived["id"]]})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), {"deleted": 1})
        self.assertEqual(self.client.get("/api/documents/" + archived["id"] + "/pdf").status_code, 404)
        self.assertEqual(len(self.client.get("/api/documents").json()), 1)

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

    def test_refused_or_invalid_answer_retries_once_with_checked_sources(self):
        doc, _ = self.module.store.create(b"%PDF-retry-answer", "retry.pdf")
        source = "A Limitations\nOnly rated attributes are included in our method."
        self.module.store.finish(doc["id"], 1, [{"page": 1, "offset": 0, "text": source}])
        valid = {"claims": [{"text": "方法仅覆盖已有评分的属性。", "source": "E1"}]}
        for first in [
            {"claims": [], "insufficient_evidence": True},
            ValueError("模型返回了不完整的 JSON"),
            {"claims": [{"text": "有99个属性。", "source": "E1"}]},
        ]:
            with (
                self.subTest(first=first),
                patch.object(self.module.models, "llm", object()),
                patch.object(self.module.models, "encode", return_value=None),
                patch.object(self.module.models, "search_query", return_value="limitations"),
                patch.object(self.module.models, "answer", side_effect=[first, valid]) as answer,
                patch.object(self.module.models, "explain_gap") as explain,
            ):
                result = self.client.post("/api/chat", json={
                    "question": "所选文献提出了哪些局限？", "document_ids": [doc["id"]],
                }).json()
                self.assertFalse(result["insufficient_evidence"])
                self.assertEqual(result["citations"][0]["quote"], source)
                self.assertEqual(answer.call_count, 2)
                self.assertTrue(answer.call_args.kwargs["retry_note"])
                explain.assert_not_called()

    def test_unsupported_answer_uses_model_explanation_without_paper_citations(self):
        doc, _ = self.module.store.create(b"%PDF-model-explanation", "explain.pdf")
        self.module.store.finish(doc["id"], 1, [{
            "page": 1, "offset": 0, "text": "A paper about attribute mapping methods.",
        }])
        explanation = "当前片段只介绍属性映射方法，没有可核对的训练配置。可继续查看方法和附录。"
        with (
            patch.object(self.module.models, "llm", object()),
            patch.object(self.module.models, "encode", return_value=None),
            patch.object(self.module.models, "search_query", return_value="attribute mapping"),
            patch.object(self.module.models, "answer", return_value={
                "claims": [{"text": "使用99张GPU", "source": "E1"}],
            }) as answer,
            patch.object(self.module.models, "explain_gap", return_value=explanation) as explain,
        ):
            result = self.client.post("/api/chat", json={
                "question": "用了多少GPU？", "document_ids": [doc["id"]],
            }).json()
            self.assertEqual(result["answer"], explanation)
            self.assertTrue(result["insufficient_evidence"])
            self.assertEqual(result["answer_mode"], "model_explanation")
            self.assertEqual(result["citations"], [])
            self.assertEqual(answer.call_count, 2)
            explain.assert_called_once()
            self.assertIn("未支持的数值", result["validation_error"])
            with patch.object(self.module, "retrieve", return_value=[]):
                empty = self.client.post("/api/chat", json={
                    "question": "如何理解这个概念？", "document_ids": [doc["id"]],
                }).json()
            self.assertEqual(empty["answer"], explanation)
            self.assertEqual(empty["citations"], [])
            self.assertEqual(answer.call_count, 2)

    def test_multiple_claims_keep_one_contiguous_source_quote(self):
        doc, _ = self.module.store.create(b"%PDF-multiple-claims", "test.pdf")
        source = "The learning rate is 0.0003. Intervening text. The batch size is 32."
        self.module.store.finish(doc["id"], 1, [{"page": 1, "offset": 0, "text": source}])
        with (
            patch.object(self.module.models, "llm", object()),
            patch.object(self.module.models, "encode", return_value=None),
            patch.object(self.module.models, "search_query", return_value="learning rate batch"),
            patch.object(
                self.module.models,
                "answer",
                return_value={
                    "claims": [
                        {
                            "text": "学习率为0.0003",
                            "source": "E1",
                            "quote": "The learning rate is 0.0003.",
                        },
                        {"text": "批量大小为32", "source": "E1", "quote": "The batch size is 32."},
                    ],
                    "insufficient_evidence": False,
                },
            ),
        ):
            result = self.client.post(
                "/api/chat", json={"question": "learning rate batch", "document_ids": [doc["id"]]}
            ).json()
        self.assertFalse(result["insufficient_evidence"])
        self.assertEqual(len(result["citations"]), 1)
        self.assertEqual(result["citations"][0]["quote"], source)
        self.assertIn("0.0003", result["answer"])
        self.assertIn("32", result["answer"])

    def test_ordered_author_route_uses_scope_and_never_asks_model_to_guess(self):
        doc, _ = self.module.store.create(b"%PDF-authors", "paper.pdf")
        text = "A Paper\nAda First\nExample Research\nBea Second\nUniversity of Elsewhere\nAbstract\nBody"
        self.module.store.save_page(
            doc["id"],
            1,
            {"text": text, "method": "native", "width": 100, "height": 200, "blocks": []},
        )
        self.module.store.finish(
            doc["id"],
            1,
            [{"page": 1, "offset": 0, "text": text}],
            metadata={"authors": "Ada First; Bea Second"},
        )
        with patch.object(self.module.models, "answer") as model:
            result = self.client.post(
                "/api/chat", json={"question": "第2位作者及机构？", "document_ids": [doc["id"]]}
            ).json()
            self.assertFalse(result["insufficient_evidence"])
            self.assertIn("Bea Second", result["answer"])
            self.assertIn("University of Elsewhere", result["answer"])
            self.assertIn(result["citations"][0]["quote"], text)
            self.assertEqual(result["citations"][0]["document_id"], doc["id"])
            rejected = self.client.post(
                "/api/chat", json={"question": "第9位作者？", "document_ids": [doc["id"]]}
            ).json()
            self.assertTrue(rejected["insufficient_evidence"])
            self.assertEqual(rejected["citations"], [])
            model.assert_not_called()

    def test_comparison_refuses_missing_scope_and_one_sided_claims(self):
        ids = []
        for marker, text in [
            (b"a", "The encoder uses attention."),
            (b"b", "The decoder generates tokens."),
        ]:
            doc, _ = self.module.store.create(b"%PDF-compare-" + marker, "paper.pdf")
            self.module.store.finish(doc["id"], 1, [{"page": 1, "offset": 0, "text": text}])
            ids.append(doc["id"])
        with (
            patch.object(self.module.models, "llm", object()),
            patch.object(self.module.models, "encode", return_value=None),
            patch.object(self.module.models, "search_query", return_value="encoder decoder"),
            patch.object(
                self.module.models,
                "comparison_facts",
                return_value={
                    "claims": [{"text": "编码器使用注意力。", "source": "E1"}],
                    "insufficient_evidence": False,
                },
            ) as model,
            patch.object(self.module.models, "explain_gap", return_value="当前片段不足以对比两篇论文。"),
        ):
            missing = self.client.post(
                "/api/chat", json={"question": "比较两篇论文", "document_ids": [ids[0], "missing"]}
            ).json()
            self.assertTrue(missing["insufficient_evidence"])
            model.assert_not_called()
            one_sided = self.client.post(
                "/api/chat", json={"question": "比较两篇论文", "document_ids": ids}
            ).json()
            self.assertTrue(one_sided["insufficient_evidence"])
            self.assertIn("检索结果之外", one_sided["validation_error"])
            model.side_effect = [
                {
                    "claims": [{"text": "编码器使用注意力。", "source": "E1"}],
                    "insufficient_evidence": False,
                },
                {
                    "claims": [{"text": "解码器生成序列。", "source": "E2"}],
                    "insufficient_evidence": False,
                },
            ]
            both = self.client.post(
                "/api/chat", json={"question": "比较两篇论文", "document_ids": ids}
            ).json()
            self.assertFalse(both["insufficient_evidence"])
            self.assertEqual({c["document_id"] for c in both["citations"]}, set(ids))
            self.assertEqual({e["document_id"] for e in model.call_args_list[-2].args[1]}, {ids[0]})
            self.assertEqual({e["document_id"] for e in model.call_args_list[-1].args[1]}, {ids[1]})
            model.side_effect = [
                {
                    "claims": [
                        {"text": "编码器使用注意力。", "source": "E1"},
                        {"text": "原文讨论编码器。", "source": "E1"},
                    ],
                    "insufficient_evidence": False,
                },
                {
                    "claims": [{"text": "解码器生成序列。", "source": "E2"}],
                    "insufficient_evidence": False,
                },
            ]
            adjusted = self.client.post(
                "/api/chat", json={"question": "两篇相比有什么调整？", "document_ids": ids}
            ).json()
            self.assertFalse(adjusted["insufficient_evidence"])
            self.assertEqual({c["document_id"] for c in adjusted["citations"]}, set(ids))
            self.assertEqual(model.call_args.kwargs["max_claims"], 2)

    def test_metric_calculation_is_returned_with_original_table_not_an_unchecked_model_number(self):
        doc, _ = self.module.store.create(b"%PDF-metric", "paper.pdf")
        quote = "Table 1: Atlas outperforms previous systems.\nModel BLEU\nPrior 21.15\nAtlas 24.7"
        self.module.store.save_page(
            doc["id"],
            1,
            {"text": quote, "method": "native", "width": 100, "height": 200, "blocks": []},
        )
        self.module.store.finish(doc["id"], 1, [{"page": 1, "offset": 0, "text": quote}])
        table = {
            "caption": "Atlas outperforms previous systems",
            "quote": quote,
            "rows": [
                {"model": "Atlas", "values": {"EN-DE": "24.7"}},
                {"model": "Prior", "values": {"EN-DE": "21.15"}},
            ],
        }
        with (
            patch.object(self.module.models, "llm", object()),
            patch.object(self.module.models, "encode", return_value=None),
            patch.object(self.module.models, "search_query", return_value="BLEU"),
            patch.object(self.module, "read_bleu_table", return_value=table),
            patch.object(self.module.models, "answer") as model,
        ):
            result = self.client.post(
                "/api/chat",
                json={"question": "Atlas 的英德 BLEU 提升多少？", "document_ids": [doc["id"]]},
            ).json()
        self.assertFalse(result["insufficient_evidence"])
        self.assertEqual(result["calculation"]["result"], "3.55")
        self.assertEqual(result["citations"][0]["quote"], quote)
        model.assert_not_called()


if __name__ == "__main__":
    unittest.main()
