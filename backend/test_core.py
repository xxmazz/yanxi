import tempfile
import unittest
from pathlib import Path

from backend.core import (
    Store,
    chunks_for_page,
    reference_anchors,
    retrieve,
    tokens,
    validate_answer,
)


class EvidenceTests(unittest.TestCase):
    def test_chunks_keep_page_and_overlap(self):
        text = "实验参数" * 900
        chunks = list(chunks_for_page(text, 7))
        self.assertEqual(chunks[1]["offset"], 940)
        self.assertTrue(all(c["page"] == 7 for c in chunks))
        self.assertEqual(chunks[0]["text"][-160:], chunks[1]["text"][:160])

    def test_chinese_and_english_retrieval(self):
        rows = [
            {"text": "实验学习率 learning rate 0.0003", "id": 1},
            {"text": "unrelated biology experiment", "id": 2},
        ]
        self.assertEqual(retrieve(rows, "学习率是多少 learning rate")[0]["id"], 1)
        self.assertIn("学习", tokens("学习率"))
        self.assertEqual(retrieve(rows, "nonexistent"), [])

    def test_reject_invented_sources(self):
        evidence = [{"label": "E1", "text": "We use 8 attention heads in the base model."}]
        bad = [
            {"text": "8个注意力头", "source": "E9", "quote": evidence[0]["text"]},
            {
                "text": "8个注意力头",
                "source": "E1",
                "quote": "The paper says something completely different.",
            },
            {"text": "16个注意力头", "source": "E1", "quote": evidence[0]["text"]},
        ]
        for claim in bad:
            with self.assertRaises(ValueError):
                validate_answer({"claims": [claim]}, evidence)
        valid = {"text": "8个注意力头", "source": "E1", "quote": evidence[0]["text"]}
        self.assertEqual(
            validate_answer({"claims": [valid]}, evidence),
            ("8个注意力头[E1]", ["E1"], False),
        )
        self.assertTrue(validate_answer({"claims": [], "insufficient_evidence": True}, evidence)[2])

    def test_explicit_figure_recovers_caption_from_long_document(self):
        rows = [
            {"id": i, "text": "discount factor Figure 19.1 Markov reward discount " * 10}
            for i in range(20)
        ] + [
            {"id": 20, "text": "Problem: see Figure 19.18, discount factor."},
            {"id": 21, "text": "Notes\nFigure\n19.18 One trajectory. The discount factor is 0.9."},
            {"id": 22, "text": "Figure 19.180 The discount factor is 0.1."},
        ]
        result = retrieve(rows, "Figure 19.18 discount factor", limit=6)
        self.assertEqual(result[0]["id"], 21)
        self.assertEqual(len(result), 6)
        self.assertEqual(len({r["id"] for r in result}), 6)

    def test_anchors_use_original_question_and_cover_multiple_references(self):
        rows = [
            {"id": 1, "text": "Figure 2.3 result"},
            {"id": 2, "text": "Table 4.1 comparison"},
            {"id": 3, "text": "Equation (5.2) formula"},
            {"id": 4, "text": "unrelated keyword " * 5},
        ]
        question = "比较图2.3、表4.1与公式（5.2）"
        result = retrieve(rows, "unrelated keyword Figure 9.9", anchor_question=question, limit=3)
        self.assertEqual({r["id"] for r in result}, {1, 2, 3})
        self.assertEqual(reference_anchors("learning rate 0.0003 and figurehead 2.3"), [])
        self.assertEqual(reference_anchors("Fig. 2.3 and Fig. 2.3"), [("figure", "2.3")])
        # 模型扩写中的虚构图号不得抢占原始问题没有要求的证据。
        ranked = retrieve(rows, "Equation 5.2 keyword", anchor_question="keyword", limit=4)
        self.assertEqual(
            [r["score"] for r in ranked], sorted((r["score"] for r in ranked), reverse=True)
        )

    def test_persistence_dedup_and_scope_isolation(self):
        with tempfile.TemporaryDirectory() as directory:
            store = Store(Path(directory))
            first, duplicate = store.create(b"%PDF-one", "one.pdf")
            self.assertFalse(duplicate)
            same, duplicate = store.create(b"%PDF-one", "renamed.pdf")
            self.assertTrue(duplicate)
            self.assertEqual(first["id"], same["id"])
            second, _ = store.create(b"%PDF-two", "two.pdf")
            store.finish(
                first["id"],
                1,
                [{"page": 1, "offset": 0, "text": "private first document"}],
            )
            store.finish(
                second["id"],
                2,
                [{"page": 2, "offset": 0, "text": "private second document"}],
            )
            self.assertEqual(len(Store(Path(directory)).rows([])), 2)
            self.assertEqual([r["document_id"] for r in store.rows([first["id"]])], [first["id"]])
            self.assertEqual(store.rows(["' OR 1=1 --"]), [])


if __name__ == "__main__":
    unittest.main()
