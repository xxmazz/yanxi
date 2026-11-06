import json
import unittest
from unittest.mock import patch

from backend.models import LocalModels


class ModelAnswerTests(unittest.TestCase):
    def setUp(self):
        self.models = LocalModels()
        self.evidence = [{"label": "E1", "title": "A paper", "page": 2,
                          "text": "Future work includes experiments."}]

    def test_retry_passes_validation_reason_and_requires_complete_output(self):
        raw = {"claims": [{"text": "实验为未来工作。", "source": "E1"}]}
        with patch.object(self.models, "generate", return_value=json.dumps(raw)) as generate:
            self.assertEqual(self.models.answer("有哪些局限？", self.evidence, retry_note="数值不匹配"), raw)
            self.assertIn("数值不匹配", generate.call_args.args[0][0]["content"])
            self.assertTrue(generate.call_args.kwargs["require_complete"])

    def test_gap_explanation_has_no_fabricated_citations_and_keeps_scope_boundary(self):
        with patch.object(self.models, "generate", return_value="当前片段把实验列为未来工作，未能核对实验结果。") as generate:
            self.assertIn("未来工作", self.models.explain_gap("实验结果？", self.evidence))
            self.assertIn("不能从记忆或猜测补出该论文的事实", generate.call_args.args[0][0]["content"])
            self.assertTrue(generate.call_args.kwargs["require_complete"])
        for output in ["", "未经核对的答案[E1]"]:
            with self.subTest(output=output), patch.object(self.models, "generate", return_value=output):
                with self.assertRaises(ValueError):
                    self.models.explain_gap("问题", [])

    def test_retry_focuses_future_work_without_relabeling_or_mutating_evidence(self):
        text = ("Unrelated introduction. " * 15 +
                "Our future work includes implementing methods and conducting experiments.\n"
                "Acknowledgments\nThanks to our sponsors.")
        evidence = [dict(self.evidence[0], label="E4", text=text)]
        with patch.object(self.models, "generate", return_value='{"claims":[],"insufficient_evidence":true}') as generate:
            self.models.answer("有哪些局限？", evidence, retry_note="重读")
            context = generate.call_args.args[0][1]["content"]
            self.assertIn("[E4]", context)
            self.assertIn("conducting experiments", context)
            self.assertNotIn("Thanks to our sponsors", context)
            self.assertLess(context.count("Unrelated introduction"), 5)
            self.assertEqual(evidence[0]["text"], text)


if __name__ == "__main__":
    unittest.main()
