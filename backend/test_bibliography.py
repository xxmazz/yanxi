import unittest

from backend.bibliography import author_ordinal, ordered_author


class BibliographyTests(unittest.TestCase):
    def setUp(self):
        self.document = {"authors": "Ada First; Bea Second; Cora Last"}
        self.text = (
            "Example Paper\nAda First*\nExample Research\nada@example.org\n"
            "Bea Second†\nUniversity of Elsewhere\nbea@example.org\n"
            "Cora Last\ncora@example.org\nAbstract\nA study.\n†Work performed at Example Research.\n"
        )
        self.page = {"method": "native", "text": self.text}

    def test_ordinal_and_footnote_come_from_corresponding_block(self):
        result = ordered_author(self.document, self.page, 2)
        self.assertEqual(result["name"], "Bea Second")
        self.assertEqual(result["affiliations"], ["University of Elsewhere"])
        self.assertEqual(result["notes"], ["†Work performed at Example Research."])
        self.assertIn(result["quote"], self.text)
        self.assertNotIn("Cora Last", result["quote"])

    def test_unverifiable_order_or_ocr_is_not_guessed(self):
        reversed_names = {"authors": "Cora Last; Ada First; Bea Second"}
        self.assertIsNone(ordered_author(reversed_names, self.page, 2))
        parallel = dict(
            self.page,
            text="A Paper\nAda First\nBea Second\nCora Last\nUniversity of Elsewhere\nAbstract\nBody",
        )
        self.assertIsNone(ordered_author(self.document, parallel, 3))
        self.assertIsNone(ordered_author(self.document, dict(self.page, method="ocr"), 2))
        self.assertIsNone(
            ordered_author(
                self.document, dict(self.page, text=self.text.replace("\nAbstract\n", "\n")), 2
            )
        )

    def test_missing_institution_is_not_filled_from_an_email_or_other_author(self):
        result = ordered_author(self.document, self.page, 3)
        self.assertEqual(result["affiliations"], [])
        self.assertEqual(result["notes"], [])
        self.assertTrue(ordered_author(self.document, self.page, 4)["out_of_range"])

    def test_question_ordinals_and_multiple_requests(self):
        self.assertEqual(author_ordinal("第6位作者的机构？"), 6)
        self.assertEqual(author_ordinal("第十二名作者是谁？"), 12)
        self.assertEqual(author_ordinal("第2作者和第3作者"), -1)
        self.assertIsNone(author_ordinal("这篇论文有多少作者？"))
        self.assertIsNone(author_ordinal("第3章介绍哪位作者？"))
        self.assertIsNone(author_ordinal("第3位作者提出了什么理论？"))
