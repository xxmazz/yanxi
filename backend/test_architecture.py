import unittest

from backend.architecture import architecture_comparison


class ArchitectureTests(unittest.TestCase):
    def setUp(self):
        self.evidence = [
            {
                "document_id": "one",
                "label": "E3",
                "text": "The Aster follows this overall architecture using attention for both the encoder and decoder.",
            },
            {
                "document_id": "two",
                "label": "E5",
                "text": "Nova’s model architec-\nture is a multi-layer bidirectional Transformer en-\ncoder based on an earlier implementation.",
            },
        ]

    def test_comparison_comes_from_two_explicit_predicates_not_model_names(self):
        result = architecture_comparison("比较架构的 Encoder 和 Decoder", self.evidence)
        self.assertIn("Nova", result["answer"])
        self.assertIn("Aster", result["answer"])
        self.assertIn("双向", result["answer"])
        self.assertIn("归纳", result["answer"])
        self.assertEqual([e["label"] for e in result["sources"]], ["E1", "E2"])
        self.assertEqual({e["document_id"] for e in result["sources"]}, {"one", "two"})

    def test_negation_ambiguous_profiles_and_missing_other_document_do_not_trigger(self):
        negative = dict(
            self.evidence[0],
            text=self.evidence[0]["text"].replace("using attention", "not using attention"),
        )
        self.assertIsNone(architecture_comparison("比较架构 Encoder", [negative, self.evidence[1]]))
        self.assertIsNone(architecture_comparison("比较架构 Encoder", self.evidence[:1]))
        conflict = dict(
            self.evidence[1],
            text="Other's model architecture is a multi-layer Transformer encoder.",
        )
        self.assertIsNone(architecture_comparison("比较架构 Encoder", self.evidence + [conflict]))
        self.assertIsNone(architecture_comparison("比较训练数据", self.evidence))
