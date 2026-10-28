import unittest

from backend.comparison import (
    comparison_priority,
    comparison_topic,
    expand_comparison_evidence,
    explicit_configuration_facts,
)
from backend.core import chunks_for_page, retrieve, validate_answer


class ComparisonRetrievalTests(unittest.TestCase):
    def test_question_dimension_is_distinguished_from_other_numbers(self):
        self.assertEqual(comparison_topic("比较最大上下文 Token 长度"), "context")
        self.assertEqual(comparison_topic("Compare parameter counts"), "parameters")
        self.assertEqual(comparison_topic("LN 位置相比有何调整？"), "normalization")
        self.assertIsNone(comparison_topic("比较准确率"))
        self.assertGreater(comparison_priority("contiguous sequences of 384 tokens", "context"), 0)
        self.assertEqual(comparison_priority("batch size 384 tokens", "context"), 0)
        self.assertGreater(comparison_priority("9 million parameters", "parameters"), 0)
        self.assertGreater(
            comparison_priority("Layer normalization was moved to the input", "normalization"), 0
        )

    def test_three_papers_each_get_two_dimension_evidence_slots(self):
        rows = [
            {"id": i, "document_id": "long", "text": "model context GPT " * 30} for i in range(70)
        ]
        for doc in ("long", "short", "third"):
            for value in (384, 768):
                rows.append(
                    {
                        "id": len(rows),
                        "document_id": doc,
                        "text": f"We train on contiguous sequences of {value} tokens.",
                    }
                )
        result = retrieve(
            rows,
            "GPT model context",
            anchor_question="分别比较上下文长度",
            coverage_ids=["long", "short", "third"],
        )
        self.assertEqual(len(result), 6)
        self.assertEqual(len({r["id"] for r in result}), 6)
        self.assertTrue(all("sequences of" in r["text"] for r in result))
        self.assertEqual([r["document_id"] for r in result], ["long", "short", "third"] * 2)
        # 模型扩写的 context 词不能覆盖用户真正问的准确率。
        untriggered = retrieve(
            rows,
            "GPT model context",
            anchor_question="分别比较准确率",
            coverage_ids=["long", "short", "third"],
        )
        self.assertNotIn("sequences of", untriggered[0]["text"])

    def test_adjacent_evidence_is_continuous_bounded_and_stays_on_original_page(self):
        text = "Parameters Layers\n" + "configuration details\n" * 100
        rows = [dict(r, document_id="paper") for r in chunks_for_page(text, 7)]
        result = expand_comparison_evidence(rows[:1], rows, "parameters")[0]
        self.assertGreater(len(result["text"]), len(rows[0]["text"]))
        self.assertLessEqual(len(result["text"]), 2000)
        self.assertIn(result["text"], text)
        self.assertEqual(result["page"], 7)
        wrong_page = [dict(r, page=8) for r in rows[1:]]
        self.assertEqual(expand_comparison_evidence(rows[:1], wrong_page, "parameters"), rows[:1])
        broken = [rows[0], dict(rows[1], text="different contents")]
        self.assertEqual(expand_comparison_evidence(rows[:1], broken, "parameters"), rows[:1])
        self.assertEqual(expand_comparison_evidence(rows[:1], rows, None), rows[:1])

    def test_table_units_and_cross_statement_inference_come_from_values(self):
        text = "Parameters Layers dmodel\n83M 8 512\n910M 32 1024\nTable 1. Configurations\nThe smallest model is equivalent to the original Aster. Our largest model, which we call Nova, is larger."
        item = {"label": "E2", "text": text}
        raw = explicit_configuration_facts("比较参数量", [item])
        answer, _, refused = validate_answer(raw, [item])
        self.assertFalse(refused)
        self.assertIn("83M", answer)
        self.assertIn("910M", answer)
        self.assertIn("归纳", answer)
        self.assertIn("Nova", answer)
        self.assertIsNone(
            explicit_configuration_facts(
                "比较参数量", [{"label": "E2", "text": text.split("Table 1.")[0]}]
            )
        )
        named = {
            "label": "E4",
            "text": "Model Name nparams nlayers dmodel nheads dhead Batch Size Learning Rate\nAster Small 83M 8 512 8 64 1M 0.01\nAster Big 91B 48 1024 16 64 2M 0.005\nTable 2.",
        }
        raw = explicit_configuration_facts("比较参数量", [named])
        answer, _, _ = validate_answer(raw, [named])
        self.assertIn("91B", answer)
        self.assertNotIn("亿", answer)
        self.assertIsNone(
            explicit_configuration_facts(
                "比较参数量", [{"label": "E4", "text": named["text"].split("Table 2.")[0]}]
            )
        )

    def test_partial_configuration_is_not_invented_total_or_ln_position(self):
        item = {
            "label": "E1",
            "text": "We trained a 9-layer decoder-only transformer. Since layernorm [2] is used extensively throughout the model, initialization is simple.",
        }
        raw = explicit_configuration_facts("比较参数量", [item])
        self.assertIn("没有直接报告总参数量", raw["claims"][0]["text"])
        self.assertIsNone(
            explicit_configuration_facts(
                "比较参数量", [dict(item, text=item["text"] + " It has 83 million parameters.")]
            )
        )
        raw = explicit_configuration_facts("比较 LN 位置", [item])
        self.assertIn("未说明", raw["claims"][0]["text"])
        self.assertIsNone(explicit_configuration_facts("比较准确率", [item]))
