"""图谱方向、原文坐标、版本计数和范围一致性回归。"""

import importlib
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient

from backend.core import Store, chunks_for_page
from backend.graph import (
    abstract_excerpt,
    build_graph,
    match_evidence,
    match_reference,
    references,
    sha,
)

TITLE = "Attention Is All You Need"


def page(text, number=1):
    return {"page": number, "text": text, "method": "native"}


def target(title=TITLE, ids=(), dois=()):
    return {"titles": [title], "arxiv_ids": list(ids), "dois": list(dois)}


class GraphTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.store = Store(Path(self.temp.name))

    def tearDown(self):
        self.temp.cleanup()

    def test_report_abstract_after_cover_and_contents_uses_real_page(self):
        pages = [
            page("Report cover", 1),
            page("Copyright", 2),
            page("Table of Contents\n1 Abstract ........... 1\n2 Introduction .......... 3", 3),
            page("List of Figures", 4),
            page(
                "1 Abstract\nThis report evaluates a retrieval pipeline using actual health documents.\n1.1 Authors' contributions\nPrivate attribution details.",
                5,
            ),
        ]
        result = abstract_excerpt("report", pages)
        self.assertEqual(result["page"], 5)
        self.assertEqual(
            result["quote"].strip(),
            "This report evaluates a retrieval pipeline using actual health documents.",
        )
        self.assertEqual(pages[4]["text"][result["start"] : result["end"]], result["quote"])
        self.assertEqual(result["page_sha256"], sha(pages[4]["text"]))

    def test_abstract_avoids_contents_and_body_mentions_and_bounds_front_pages(self):
        self.assertIsNone(
            abstract_excerpt(
                "doc",
                [
                    page(
                        "Table of Contents\nAbstract ......... 3\n1 Introduction ......... 4\nWe discuss abstract reasoning here."
                    )
                ],
            )
        )
        self.assertIsNone(
            abstract_excerpt(
                "doc",
                [page("Cover", n) for n in range(1, 9)]
                + [page("Abstract\nA body section unrelated to the front abstract.", 9)],
            )
        )

    def test_abstract_stops_at_roman_intro_and_keywords(self):
        for ending in [
            "I. INTRODUCTION",
            "I. I NTRODUCTION",
            "Keywords: retrieval",
            "Index Terms—retrieval",
        ]:
            with self.subTest(ending=ending):
                result = abstract_excerpt(
                    "doc",
                    [
                        page(
                            "Abstract—We evaluate a local retrieval pipeline.\n"
                            + ending
                            + "\nUnrelated body."
                        )
                    ],
                )
                self.assertEqual(result["quote"], "We evaluate a local retrieval pipeline.\n")

    def add(self, title, texts, **metadata):
        doc, _ = self.store.create(
            f"%PDF-{title}-{metadata}-{len(self.store.documents())}".encode(), "test.pdf"
        )
        chunks = []
        for number, text in enumerate(texts, 1):
            self.store.save_page(
                doc["id"],
                number,
                {
                    "text": text,
                    "method": "ocr" if metadata.pop("scan", False) else "native",
                    "width": 100,
                    "height": 100,
                    "blocks": [],
                },
            )
            chunks.extend(chunks_for_page(text, number))
        self.store.finish(doc["id"], len(texts), chunks, title=title, metadata=metadata)
        return doc["id"]

    def test_body_mentions_do_not_become_edges(self):
        self.add(TITLE, ["Abstract\nA transformer paper.\n1 Introduction\nBody"])
        self.add("Research without bibliography", [f"We discuss {TITLE} in our body."])
        graph = build_graph(self.store)
        self.assertEqual(graph["edges"], [])
        self.assertEqual(len(graph["warnings"]), 2)

    def test_full_title_cross_page_and_raw_offsets(self):
        pages = [
            page("Body\nReferences\nVaswani. Atten-\ntion Is All", 3),
            page("You Need. 2017.\nOther reference", 4),
        ]
        section = references(pages)
        match = match_reference(section, target())
        self.assertIsNotNone(match)
        evidence = match_evidence("doc", section, *match[:2])
        self.assertEqual([e["page"] for e in evidence], [3, 4])
        for item, original in zip(evidence, pages):
            self.assertEqual(item["quote"], original["text"][item["start"] : item["end"]])
            self.assertEqual(item["page_sha256"], sha(original["text"]))
        self.assertIsNone(
            match_reference(
                references([page("References\nAttention Is All You Needed.")]), target()
            )
        )
        self.assertIsNone(
            match_reference(
                references([page("References\nAttention Is All You Needless.")]), target()
            )
        )
        self.assertIsNone(
            match_reference(
                references([page("References\nAttention Is All You Need for Image Retrieval.")]),
                target(),
            )
        )

    def test_identifier_boundaries_and_doi(self):
        for raw in ["arXiv:1706.03762v7.", "https://arxiv.org/abs/1706.03762"]:
            self.assertEqual(
                match_reference({"text": raw}, target("short", ["1706.03762"]))[2], "arxiv_id"
            )
        for raw in ["11706.03762", "1706.037620", "1706.03762.1", "x1706.03762", "1706.03762v7x"]:
            self.assertIsNone(match_reference({"text": raw}, target("short", ["1706.03762"])))
        self.assertEqual(
            match_reference(
                {"text": "doi:10.1000/xyz123."}, target("short", dois=["10.1000/xyz123"])
            )[2],
            "doi",
        )
        self.assertIsNone(
            match_reference({"text": "10.1000/xyz1234"}, target("short", dois=["10.1000/xyz123"]))
        )

    def test_appendix_does_not_create_citation(self):
        for heading in [
            "Appendix",
            "A Additional experiments",
            "Supplementary material",
            "A Related Work",
            "A. Estimating pass@k",
            "A Question Answering",
            "A L ARGE LANGUAGE MODELS STILL NEED PARAMETER UPDATES",
            "A BIG-Bench analysis",
            "Checklist",
            "Attention Visualizations",
        ]:
            section = references([page(f"References\nOther paper.\n{heading}\nWe discuss {TITLE}")])
            self.assertIsNone(match_reference(section, target()))

    def test_bibliographic_titles_and_author_initials_do_not_stop_references(self):
        section = references(
            [
                page(
                    "References\nA framework for sequence learning.\nA simple and language independent method.\nS. On the dangers of parrots.\n"
                    + TITLE
                    + "."
                )
            ]
        )
        self.assertIsNotNone(match_reference(section, target()))

    def test_ligatures_and_short_titles(self):
        match = match_reference(
            {"text": "Efﬁcient scientiﬁc document retrieval."},
            target("Efficient scientific document retrieval"),
        )
        self.assertEqual(match[2], "full_title")
        self.assertIsNone(
            match_reference({"text": "References: A short title"}, target("A short title"))
        )

    def test_title_suffix_of_another_work_is_not_an_edge(self):
        for text in [
            "Biao Zhang. Root mean square layer normalization. 2019.",
            "Biao Zhang. 2019. Root mean square\nlayer normalization.",
        ]:
            self.assertIsNone(match_reference({"text": text}, target("Layer Normalization")))
        self.assertIsNotNone(
            match_reference(
                {"text": "Jimmy Ba. Layer normalization, 2016."}, target("Layer Normalization")
            )
        )

    def test_versions_merge_and_newest_outgoing_edges_only(self):
        a = self.add(
            TITLE,
            ["Abstract\nTransformer original abstract.\nReferences\n"],
            authors="Ashish Vaswani; Other",
            arxiv_id="1706.03762",
            version=7,
        )
        b = self.add(
            TITLE,
            ["References\nOther scientific foundational paper"],
            authors="Ashish Vaswani; Other",
            scan=True,
        )
        c = self.add("Other scientific foundational paper", [f"References\n{TITLE}\n{TITLE}"])
        graph = build_graph(self.store)
        self.assertEqual(graph["stats"]["works"], 2)
        self.assertEqual(graph["stats"]["edges"], 1)
        transformer = next(n for n in graph["nodes"] if n["title"] == TITLE)
        self.assertEqual({d["id"] for d in transformer["documents"]}, {a, b})
        self.assertEqual(transformer["document_id"], a)
        self.assertEqual(transformer["in_degree"], 1)
        self.assertEqual(transformer["out_degree"], 0)
        self.assertEqual(graph["edges"][0]["source"], "document:" + c)
        self.assertGreater(
            transformer["pagerank"],
            next(n for n in graph["nodes"] if n["document_id"] == c)["pagerank"],
        )

    def test_different_authors_same_title_not_merged(self):
        for author in ("A Author", "B Author"):
            self.add(TITLE, ["References\nNothing"], authors=author)
        self.assertEqual(build_graph(self.store)["stats"]["works"], 2)

    def test_conflicting_identifiers_do_not_merge(self):
        for arxiv_id in ("1706.03762", "", "2010.00001"):
            self.add(TITLE, ["References\nNothing"], authors="A Author", arxiv_id=arxiv_id)
        self.assertEqual(build_graph(self.store)["stats"]["works"], 2)

    def test_twenty_papers_direction_degree_and_determinism(self):
        titles = [f"Scientific foundational methods number {i:02d}" for i in range(20)]
        for i, title in enumerate(titles):
            self.add(title, ["References\n" + "\n".join(t + "." for t in titles[:i])])
        graph = build_graph(self.store)
        self.assertEqual(graph["stats"]["works"], 20)
        self.assertEqual(graph["stats"]["edges"], 190)
        self.assertEqual(graph["stats"]["components"], 1)
        top = max(graph["nodes"], key=lambda n: n["pagerank"])
        self.assertEqual(top["title"], titles[0])
        self.assertEqual(top["in_degree"], 19)
        self.assertEqual(top["out_degree"], 0)
        self.assertAlmostEqual(sum(n["pagerank"] for n in graph["nodes"]), 1, places=6)
        repeat = build_graph(self.store)
        self.assertEqual(graph["nodes"], repeat["nodes"])
        self.assertEqual(graph["fingerprint"], repeat["fingerprint"])

    def test_scope_archive_pending_and_incomplete_pages(self):
        a = self.add(TITLE, ["References\nnone"])
        b = self.add("Pending scientific review document", ["References\nnone"])
        with self.store.connect() as db:
            db.execute("UPDATE documents SET status='review' WHERE id=?", (b,))
        self.assertEqual(build_graph(self.store)["document_ids"], [a])
        with self.assertRaises(ValueError):
            build_graph(self.store, [b])
        self.store.trash(a)
        self.assertEqual(build_graph(self.store)["stats"]["works"], 0)
        with self.assertRaises(ValueError):
            build_graph(self.store, [a])
        self.store.trash(a, restore=True)
        with self.store.connect() as db:
            db.execute("DELETE FROM pages WHERE document_id=?", (a,))
        with self.assertRaisesRegex(ValueError, "页数据不完整"):
            build_graph(self.store)

    def test_empty_and_limit(self):
        self.assertEqual(build_graph(self.store)["nodes"], [])
        ids = [
            self.add(f"Scientific document number {i:02d}", ["References\nnone"]) for i in range(101)
        ]
        with self.assertRaisesRegex(ValueError, "100"):
            build_graph(self.store)
        self.assertEqual(build_graph(self.store, ids[:20])["stats"]["documents"], 20)
        self.assertEqual(build_graph(self.store, ids[:100])["stats"]["documents"], 100)

    def test_fingerprint_tracks_metadata_and_page_changes(self):
        a = self.add(TITLE, ["References\nnone"])
        previous = build_graph(self.store)["fingerprint"]
        for sql, args in [
            ("UPDATE documents SET arxiv_id=? WHERE id=?", ("1706.03762", a)),
            ("UPDATE pages SET text=? WHERE document_id=?", ("References\nchanged", a)),
            ("UPDATE pages SET method=? WHERE document_id=?", ("ocr", a)),
        ]:
            with self.store.connect() as db:
                db.execute(sql, args)
            current = build_graph(self.store)["fingerprint"]
            self.assertNotEqual(current, previous)
            previous = current

    def test_future_work_is_exact_body_excerpt(self):
        text = "Abstract\nAn original abstract.\n1 Introduction\nFuture work will assess transfer.\nReferences\nFuture work in unrelated citation."
        self.add(TITLE, [text])
        node = build_graph(self.store)["nodes"][0]
        self.assertEqual(len(node["future_work"]), 1)
        self.assertNotIn("unrelated", node["future_work"][0]["quote"])
        self.assertEqual(node["abstract_evidence"]["quote"].strip(), "An original abstract.")

    def test_api_review_scope_staleness_and_numeric_guard(self):
        module = importlib.import_module("backend.app")
        self.add(
            TITLE,
            [
                "Abstract\nThe model uses attention for translation.\n1 Introduction\nReferences\nnone"
            ],
        )
        with (
            patch.object(module, "store", self.store),
            patch.object(module.models, "llm", object()),
        ):
            client = TestClient(module.app)
            graph = client.post("/api/graph/build", json={}).json()
            response = client.post("/api/graph/review", json={"fingerprint": "0" * 64})
            self.assertEqual(response.status_code, 409)

            self.assertEqual(
                client.post("/api/graph/build", json={"document_ids": ["missing"]}).status_code, 409
            )
            self.assertEqual(
                client.post("/api/graph/build", json={"document_ids": ["x"] * 101}).status_code, 422
            )
            for text, expected in [
                ("使用注意力机制翻译。", False),
                ("模型使用 999 张显卡。", True),
            ]:
                raw = {
                    "claims": [
                        {
                            "text": text,
                            "source": "E1",
                            "quote": "The model uses attention for translation.",
                        }
                    ]
                }
                with patch.object(module.models, "answer", return_value=raw):
                    response = client.post(
                        "/api/graph/review",
                        json={
                            "fingerprint": graph["fingerprint"],
                            "document_ids": graph["document_ids"],
                        },
                    )
                self.assertEqual(response.status_code, 200)
                self.assertEqual(response.json()["insufficient_evidence"], expected)
                self.assertEqual(len(response.json()["citations"]), 1)

            def changed_during_review(*_):
                with self.store.connect() as db:
                    db.execute("UPDATE pages SET text=text || ' changed'")
                return {
                    "claims": [
                        {
                            "text": "使用注意力机制。",
                            "source": "E1",
                            "quote": "The model uses attention for translation.",
                        }
                    ]
                }

            with patch.object(module.models, "answer", side_effect=changed_during_review):
                response = client.post(
                    "/api/graph/review",
                    json={
                        "fingerprint": graph["fingerprint"],
                        "document_ids": graph["document_ids"],
                    },
                )
            self.assertEqual(response.status_code, 409)

            self.add(
                "Grounded generation evaluation research",
                [
                    "Abstract\nThe method evaluates grounded generation.\n1 Introduction\nReferences\nnone"
                ],
            )
            graph = client.post("/api/graph/build", json={}).json()
            with patch.object(
                module.models,
                "answer",
                return_value={"claims": [{"text": "仅总结一篇论文。", "source": "E1"}]},
            ):
                response = client.post(
                    "/api/graph/review",
                    json={
                        "fingerprint": graph["fingerprint"],
                        "document_ids": graph["document_ids"],
                    },
                )
            self.assertTrue(response.json()["insufficient_evidence"])

    def test_review_reads_each_abstract_in_isolation_before_merging(self):
        module = importlib.import_module("backend.app")
        for index in range(3):
            self.add(
                f"Research on reliable source isolation {index}",
                [
                    "Abstract\nWe propose attention for translation.\n1 Introduction\nReferences\nnone"
                ],
            )

        def summarize(_question, evidence):
            self.assertEqual(len(evidence), 1)
            return {
                "claims": [{"text": "提出用于翻译的注意力方法。", "source": evidence[0]["label"]}]
            }

        with (
            patch.object(module, "store", self.store),
            patch.object(module.models, "llm", object()),
            patch.object(module.models, "answer", side_effect=summarize) as mocked,
        ):
            client = TestClient(module.app)
            graph = client.post("/api/graph/build", json={}).json()
            response = client.post("/api/graph/review", json={"fingerprint": graph["fingerprint"]})
            self.assertEqual(response.status_code, 200)
            result = response.json()
            self.assertFalse(result["insufficient_evidence"])
            self.assertEqual(mocked.call_count, 3)
            self.assertEqual([c["id"] for c in result["citations"]], ["E1", "E2", "E3"])
