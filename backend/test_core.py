import tempfile
import unittest
from pathlib import Path

from backend.core import Store, chunks_for_page, retrieve, tokens, validate_answer


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
