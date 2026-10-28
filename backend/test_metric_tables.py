import unittest
from pathlib import Path
from unittest.mock import patch

from backend.metric_tables import bleu_column, read_bleu_table, table_answer, table_from_words


def word(text, x, y):
    return {"text": text, "x0": x - 8, "x1": x + 8, "y0": y, "y1": y + 8}


class MetricTableTests(unittest.TestCase):
    def setUp(self):
        self.words = [
            word("BLEU", 320, 90),
            word("EN-DE", 300, 100),
            word("EN-FR", 340, 100),
            word("EN-DE", 410, 100),
            word("EN-FR", 460, 100),
        ]
        for y, label, left, right in [
            (112, "Earlier", None, "90"),
            (124, "Prior", "21.15", "29.2"),
            (136, "Atlas", "24.7", "31.5"),
        ]:
            self.words.append(word(label, 150, y))
            if left:
                self.words.append(word(left, 300, y))
            self.words.append(word(right, 340, y))
        self.table = table_from_words(self.words)
        self.table["caption"] = "Table 4: Atlas compared with previous state-of-the-art models."

    def test_missing_cell_keeps_its_column_and_training_cost_headers_are_ignored(self):
        self.assertIsNone(self.table["rows"][0]["values"]["EN-DE"])
        self.assertEqual(self.table["rows"][0]["values"]["EN-FR"], "90")
        self.assertEqual(self.table["rows"][2]["values"]["EN-DE"], "24.7")

    def test_difference_is_decimal_and_uses_same_column(self):
        result = table_answer("Atlas 的英德 BLEU 比之前提升多少？", self.table)
        self.assertEqual(result["calculation"]["right"], "21.15")
        self.assertEqual(result["calculation"]["result"], "3.55")
        self.assertIn("程序计算", result["answer"])
        self.assertNotIn("90", result["answer"])

    def test_ambiguous_metric_layout_and_requested_column_are_not_guessed(self):
        self.assertIsNone(table_from_words(self.words + [word("22", 300, 136)]))
        self.assertIsNone(bleu_column("Atlas 英德和英法 BLEU"))
        self.assertTrue(
            table_answer("Earlier 在英德的 BLEU 是多少？", self.table)["insufficient_evidence"]
        )
        self.assertIsNone(table_answer("Atlas 和 Prior 的英德 BLEU 对比", self.table))

    def test_table_has_to_describe_the_proposed_family_for_previous_best_comparison(self):
        self.assertIsNone(table_answer("Prior 英德 BLEU提升多少？", self.table))
        result = table_answer("Atlas EN-FR BLEU 提升多少？", self.table)
        self.assertEqual(result["calculation"]["result"], "-58.5")

    def test_unavailable_pdf_tool_does_not_invent_a_table(self):
        with patch("backend.metric_tables.subprocess.run", side_effect=FileNotFoundError):
            self.assertIsNone(read_bleu_table(Path("missing.pdf"), 1, "Table 1: Example\nModel"))
