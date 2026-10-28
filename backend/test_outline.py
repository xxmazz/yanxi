import unittest

from backend.outline import outline


def page(number, text):
    return {"page": number, "text": text, "method": "native"}


class OutlineTests(unittest.TestCase):
    def test_three_levels_unicode_offsets_and_references_stop(self):
        pages = [
            page(
                1,
                "😀 Title\nAbstract\n1 Introduction\n1.1 Method\n1.1.1 Attention\n2 Evaluation\n2.1 SQuAD v1.1\n42\nReferences\n1 Not a chapter\nA Appendix",
            )
        ]
        items = outline("d", pages)
        self.assertEqual([i["path"] for i in items], ["", "1", "1.1", "1.1.1", "2", "2.1", ""])
        self.assertEqual(items[-1]["title"], "References")
        for item in items:
            self.assertEqual(pages[0]["text"][item["start"] : item["end"]], item["quote"])
        self.assertEqual(items[-1]["quote"], "References")

    def test_missing_parent_tables_sentences_and_running_header(self):
        result = outline(
            "d",
            [
                page(
                    1,
                    "3.2 Orphan\n1 Introduction\n1 Introduction\n2 Model 100 200\n2 The model is trained.\n3 x = y\n2 Method",
                ),
                page(2, "1 Introduction\n2.1 Evaluation"),
            ],
        )
        self.assertEqual(
            [(i["path"], i["title"]) for i in result],
            [("1", "Introduction"), ("2", "Method"), ("2.1", "Evaluation")],
        )

    def test_unstructured_text_remains_without_invented_headings(self):
        self.assertEqual(
            outline(
                "d",
                [
                    page(
                        1,
                        "Introduction to this work is in a paragraph.\n2024 Annual Report\n98 Experiment",
                    )
                ],
            ),
            [],
        )


class BlankHeadingTests(unittest.TestCase):
    def test_reference_heading_with_blank_lines(self):
        result = outline("d", [page(1, "1 Introduction\nBody\nReferences\n\n[1] Title.")])
        self.assertEqual(result[-1]["title"], "References")
