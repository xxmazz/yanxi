"""写作计划、真实书目、引用边界及原文快照测试。"""

import copy
import importlib
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient

from backend.core import Store, chunks_for_page
from backend.models import LocalModels
from backend.writing import diagram_for, prepare, render_draft


class WritingTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.store = Store(Path(self.temp.name))
        self.ids = []
        self.add(
            "Local evidence research methods",
            "The method uses 8 GPUs and local evidence for retrieval.",
        )

    def tearDown(self):
        self.temp.cleanup()

    def add(self, title, abstract, **metadata):
        doc, _ = self.store.create(f"%PDF-{title}".encode(), title + ".pdf")
        text = "Abstract\n" + abstract + "\n1 Introduction\nA body.\nReferences\nNone"
        self.store.save_page(
            doc["id"],
            1,
            {"text": text, "method": "native", "width": 100, "height": 100, "blocks": []},
        )
        self.store.finish(
            doc["id"],
            1,
            list(chunks_for_page(text, 1)),
            title,
            metadata={"authors": "A. Author", **metadata},
        )
        self.ids.append(doc["id"])
        return doc["id"]

    def raw(self, bundle):
        return {
            "title": "有证据的科研检索研究计划",
            "problem": "科研回答的证据可追溯性",
            "approach": ["构建本地文献索引", "检索原文并核对引用"],
            "evaluation": ["人工核验来源正确性", "评估证据缺失的失败情况"],
            "claims": [
                {"text": "研究提出使用本地证据开展检索的方法。", "source": e["label"]}
                for e in bundle["evidence"]
            ],
        }

    def test_real_bibliography_and_proposed_method(self):
        bundle = prepare(self.store, self.ids)
        result = render_draft("研究可靠科研问答", self.raw(bundle), bundle)
        for heading in (
            "Abstract",
            "Introduction",
            "Related Work",
            "Proposed Method",
            "Evaluation Plan",
            "References",
        ):
            self.assertIn(heading, result["markdown"])
        self.assertIn("[1] A. Author. Local evidence research methods.", result["markdown"])
        self.assertIn("拟开展", result["markdown"])
        source = result["references"][0]["evidence"]
        self.assertEqual(
            self.store.page(source["document_id"], source["page"])["text"][
                source["start"] : source["end"]
            ],
            source["quote"],
        )
        self.assertEqual(result["diagram"]["nodes"][1]["label"], "构建本地文献索引")

    def test_invented_bibliography_from_model_is_never_used(self):
        bundle = prepare(self.store, self.ids)
        raw = self.raw(bundle)
        raw["references"] = [{"title": "A nonexistent paper", "doi": "10.9999/fake"}]
        self.assertNotIn("nonexistent", render_draft("科研设想", raw, bundle)["markdown"])
        raw["claims"][0]["source"] = "E99"
        with self.assertRaises(ValueError):
            render_draft("科研设想", raw, bundle)

    def test_unsupported_numbers_and_nonverbatim_quote_rejected(self):
        bundle = prepare(self.store, self.ids)
        for change in [
            {"text": "研究使用 999 张显卡。"},
            {"quote": "A fabricated quotation not in the paper."},
        ]:
            raw = self.raw(bundle)
            raw["claims"][0].update(change)
            with self.assertRaises(ValueError):
                render_draft("科研设想", raw, bundle)
        raw = self.raw(bundle)
        raw["claims"][0]["text"] = "研究使用 8 张 GPU。"
        self.assertIn("8", render_draft("科研设想", raw, bundle)["markdown"])

    def test_all_sources_must_be_covered(self):
        self.add(
            "A different scientific grounding paper",
            "The work examines source grounding and citation correctness.",
        )
        bundle = prepare(self.store, self.ids)
        raw = self.raw(bundle)
        raw["claims"] = raw["claims"][:1]
        with self.assertRaisesRegex(ValueError, "全部摘要"):
            render_draft("科研设想", raw, bundle)

    def test_model_cannot_embed_links_or_fake_citation_markers(self):
        bundle = prepare(self.store, self.ids)
        for field, value in [
            ("problem", "see https://evil.example/paper"),
            ("title", "虚构引用 [99]"),
            ("problem", "doi:10.9999/fake"),
        ]:
            raw = self.raw(bundle)
            raw[field] = value
            with self.assertRaises(ValueError):
                render_draft("科研设想", raw, bundle)
        for text in ["结论见 [E1]", "参见 [33]", "模型编造 https://evil.example"]:
            raw = self.raw(bundle)
            raw["claims"][0]["text"] = text
            with self.assertRaises(ValueError):
                render_draft("科研设想", raw, bundle)

    def test_plan_structure_and_truncated_output_rejected(self):
        bundle = prepare(self.store, self.ids)
        for field, value in [
            ("approach", []),
            ("evaluation", ["one"]),
            ("problem", None),
            ("approach", ["x" * 91, "b"]),
        ]:
            raw = self.raw(bundle)
            raw[field] = value
            with self.assertRaises(ValueError):
                render_draft("科研设想", raw, bundle)
        with self.assertRaises(ValueError):
            render_draft("科研设想", [], bundle)

    def test_mermaid_labels_are_data_and_nodes_are_fixed(self):
        attack = '"][evil] --> x["<script>alert(1)</script>\\'
        diagram = diagram_for([attack, "正常步骤"])
        self.assertEqual([n["id"] for n in diagram["nodes"]], ["idea", "m1", "m2", "evaluation"])
        self.assertEqual(len(diagram["edges"]), 3)
        self.assertIn("&quot;", diagram["mermaid"])
        self.assertNotIn("<script>", diagram["mermaid"])
        self.assertNotIn('"][evil]', diagram["mermaid"])

    def test_no_abstract_and_archived_scope_refused(self):
        with self.assertRaises(ValueError):
            prepare(self.store, [])
        with self.store.connect() as db:
            db.execute(
                "UPDATE pages SET text='No abstract here is not a heading' WHERE document_id=?",
                (self.ids[0],),
            )
        # 没有明确摘要时不得把任意第一页正文当作写作证据。
        with self.assertRaises(ValueError):
            prepare(self.store, self.ids)
        self.store.trash(self.ids[0])
        with self.assertRaises(ValueError):
            prepare(self.store, self.ids)

    def test_six_work_limit_and_version_grouping(self):
        self.add(
            "Local evidence research methods",
            "The method uses 8 GPUs and local evidence.",
            arxiv_id="1706.03762",
            version=7,
        )
        self.assertEqual(len(prepare(self.store, self.ids)["references"]), 1)
        for i in range(6):
            self.add(
                f"Different long scientific title number {i}",
                "The work studies grounded scholarly source retrieval.",
            )
        with self.assertRaisesRegex(ValueError, "六篇"):
            prepare(self.store, self.ids)

    def test_official_metadata_and_missing_metadata_are_explicit(self):
        from backend.discovery import ArxivDiscovery

        discovery = ArxivDiscovery(self.store)
        metadata = {
            "arxiv_id": "1706.03762",
            "title": "Official verified title",
            "authors": ["Real Author"],
            "published": "2017-06-01",
            "doi": "",
            "abstract": "A genuine abstract",
        }
        with self.store.connect() as db:
            db.execute(
                "INSERT INTO document_sources(document_id,arxiv_id,metadata) VALUES(?,?,?)",
                (self.ids[0], metadata["arxiv_id"], json.dumps(metadata)),
            )
        ref = prepare(self.store, self.ids)["references"][0]
        self.assertEqual(ref["authors"], "Real Author")
        self.assertEqual(ref["title"], "Official verified title")
        self.assertEqual(ref["source_url"], "https://arxiv.org/abs/1706.03762")
        self.assertEqual(ref["year"], 2017)
        self.assertEqual(len(discovery.sources(self.ids[0])), 1)

    def test_model_reads_each_source_and_restores_reference_labels(self):
        self.add(
            "A different scientific grounding paper",
            "The work examines source grounding and citation correctness.",
        )
        bundle = prepare(self.store, self.ids)
        model = LocalModels()

        outputs = [json.dumps(self.raw(bundle))] + [
            json.dumps({"text": "研究分析证据检索方法。"}) for _ in bundle["evidence"]
        ]
        with patch.object(model, "generate", side_effect=outputs) as calls:
            raw = model.draft_outline("科研设想", bundle["evidence"])
        self.assertEqual(calls.call_count, 3)
        for call, source in zip(calls.call_args_list[1:], bundle["evidence"]):
            self.assertIn(source["text"], call.args[0][1]["content"])
        self.assertEqual([claim["source"] for claim in raw["claims"]], ["E1", "E2"])
        result = render_draft("科研设想", raw, bundle)
        self.assertEqual(len(result["references"]), 2)

    def test_model_refusal_does_not_produce_partial_plan(self):
        bundle = prepare(self.store, self.ids)
        model = LocalModels()
        with patch.object(
            model,
            "generate",
            side_effect=[json.dumps(self.raw(bundle)), json.dumps({"insufficient_evidence": True})],
        ):
            with self.assertRaisesRegex(ValueError, "证据不足"):
                model.draft_outline("科研设想", bundle["evidence"])

    def test_plan_format_retry_is_bounded_and_preserves_validation(self):
        bundle = prepare(self.store, self.ids)
        model = LocalModels()
        outputs = [
            json.dumps({"approach": "invalid"}),
            json.dumps(self.raw(bundle)),
            json.dumps({"text": "研究分析证据检索方法。"}),
        ]
        with patch.object(model, "generate", side_effect=outputs) as calls:
            result = model.draft_outline("科研设想", bundle["evidence"])
        self.assertEqual(len(result["claims"]), 1)
        self.assertEqual(calls.call_count, 3)
        with patch.object(model, "generate", return_value='{"title":"invalid"}') as calls:
            with self.assertRaises(ValueError):
                model.draft_outline("科研设想", bundle["evidence"])
        self.assertEqual(calls.call_count, 2)

    def test_ground_truth_qualification_requires_explicit_source_condition(self):
        bundle = prepare(self.store, self.ids)
        model = LocalModels()
        for source, expected in [
            (
                "A framework without having to rely on\nground truth human annotations.",
                "不依赖人工标注的标准答案",
            ),
            ("The work examines reference-free evaluation methods.", "无需参考答案"),
        ]:
            evidence = [{**bundle["evidence"][0], "text": source}]
            with patch.object(
                model,
                "generate",
                side_effect=[
                    json.dumps(self.raw(bundle)),
                    json.dumps({"text": "该框架无需参考答案。"}),
                ],
            ):
                result = model.draft_outline("科研设想", evidence)
            self.assertIn(expected, result["claims"][0]["text"])
            self.assertEqual(result["claims"][0]["quote"], source)

    def test_api_generation_validation_staleness_and_failure_preservation(self):
        module = importlib.import_module("backend.app")
        bundle = prepare(self.store, self.ids)
        raw = self.raw(bundle)
        with (
            patch.object(module, "store", self.store),
            patch.object(module.models, "llm", object()),
        ):
            client = TestClient(module.app)
            request = {"idea": "研究证据可靠性", "document_ids": self.ids}
            with patch.object(module.models, "draft_outline", return_value=copy.deepcopy(raw)):
                result = client.post("/api/writing/outline", json=request)
            self.assertEqual(result.status_code, 200, result.text)
            check = {"document_ids": self.ids, "fingerprint": result.json()["fingerprint"]}
            self.assertTrue(client.post("/api/writing/check", json=check).json()["current"])
            with patch.object(module.models, "draft_outline", return_value={"title": "incomplete"}):
                self.assertEqual(client.post("/api/writing/outline", json=request).status_code, 422)
            self.assertEqual(
                client.post("/api/writing/outline", json={**request, "idea": "   "}).status_code,
                422,
            )
            self.assertEqual(
                client.post(
                    "/api/writing/outline", json={**request, "document_ids": []}
                ).status_code,
                422,
            )
            self.assertEqual(
                client.post(
                    "/api/writing/outline", json={**request, "document_ids": ["missing"]}
                ).status_code,
                409,
            )

            def changing(*_):
                with self.store.connect() as db:
                    db.execute("UPDATE pages SET text=text || ' changed'")
                return copy.deepcopy(raw)

            with patch.object(module.models, "draft_outline", side_effect=changing):
                self.assertEqual(client.post("/api/writing/outline", json=request).status_code, 409)
            self.assertEqual(client.post("/api/writing/check", json=check).status_code, 409)
            with patch.object(module.models, "llm", None):
                self.assertEqual(client.post("/api/writing/outline", json=request).status_code, 503)
