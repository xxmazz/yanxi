import importlib
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient

from backend.core import Store
from backend.reading_links import build_links, navigation


def page(number, text):
    return {"page": number, "text": text, "method": "native"}


class ReadingLinkTests(unittest.TestCase):
    def test_grouped_citations_cross_page_entry_and_exact_offsets(self):
        pages = [
            page(1, "😀 Methods [1, 2–3]. Unknown [99]. Too wide [1-999]."),
            page(2, "References\n[1] First title.\n[2] Second starts"),
            page(3, "and continues.\n[3] Third title.\nA Related Work\nAppendix [1]"),
        ]
        result = build_links("doc", pages)
        self.assertEqual([m["numbers"] for m in result["mentions"]], [[1, 2, 3]])
        self.assertEqual(len(result["entries"][1]["evidence"]), 2)
        for anchor in [*result["mentions"], *(a for e in result["entries"] for a in e["evidence"])]:
            text = pages[anchor["page"] - 1]["text"]
            self.assertEqual(text[anchor["start"] : anchor["end"]], anchor["quote"])
            self.assertNotIn("Appendix", anchor["quote"])

    def test_duplicate_numbers_are_not_clickable(self):
        result = build_links(
            "doc", [page(1, "Body [1] [2].\nReferences\n[1] First.\n[1] Conflicting.\n[2] Valid.")]
        )
        self.assertEqual([e["number"] for e in result["entries"]], [2])
        self.assertEqual([m["numbers"] for m in result["mentions"]], [[2]])

    def test_missing_reference_heading_and_author_year_are_not_guessed(self):
        for text in [
            "Body [1].\n[1] No heading.",
            "Body (Ba, 2016).\nReferences\nBa. Layer normalization. 2016.",
        ]:
            result = build_links("doc", [page(1, text)])
            self.assertEqual(result["entries"], [])
            self.assertEqual(result["mentions"], [])

    def test_fingerprint_changes_and_reparse_incomplete_rejected(self):
        a = build_links("doc", [page(1, "Body [1].\nReferences\n[1] Title.")])
        b = build_links("doc", [page(1, "Body changed [1].\nReferences\n[1] Title.")])
        self.assertNotEqual(a["fingerprint"], b["fingerprint"])
        with tempfile.TemporaryDirectory() as folder:
            store = Store(Path(folder))
            with self.assertRaises(KeyError):
                navigation(store, "missing")
            doc, _ = store.create(b"%PDF-navigation", "test.pdf")
            with self.assertRaises(ValueError):
                navigation(store, doc["id"])

    def test_api_routes_and_ready_state(self):
        module = importlib.import_module("backend.app")
        with tempfile.TemporaryDirectory() as folder:
            store = Store(Path(folder))
            doc, _ = store.create(b"%PDF-links-api", "test.pdf")
            base = f"/api/documents/{doc['id']}/reading-links"
            with patch.object(module, "store", store):
                client = TestClient(module.app)
                self.assertEqual(
                    client.get("/api/documents/missing/reading-links").status_code, 404
                )
                self.assertEqual(client.get(base).status_code, 409)
                store.save_page(
                    doc["id"],
                    1,
                    {
                        "text": "Body [1].\nReferences\n[1] A title.",
                        "method": "native",
                        "width": 100,
                        "height": 100,
                        "blocks": [],
                    },
                )
                store.finish(doc["id"], 1, [])
                result = client.get(base)
                self.assertEqual(result.status_code, 200)
                self.assertEqual(result.json()["mentions"][0]["numbers"], [1])
            store.finish(doc["id"], 2, [])
            with self.assertRaises(ValueError):
                navigation(store, doc["id"])


class AuthorYearTests(unittest.TestCase):
    def test_suffix_wrapped_surname_and_two_authors(self):
        body = "Peters et al., 2018a; Peters et al. (2018b). Peters et al., 2018. Howard and Ruder (2018). Rad-\nford et al., 2018. [1]"
        result = build_links(
            "doc",
            [
                page(1, body),
                page(
                    2,
                    "References\nMatthew Peters, Mark Neumann, and Kenton Lee. 2018a. First title.\nMatthew Peters, Luke Zettlemoyer, and Wen-tau Yih. 2018b. Second title.\nJeremy Howard and Sebastian Ruder. 2018. Third title.\nAlec Radford, Karthik Narasimhan, and Ilya Sutskever. 2018. Fourth title.",
                ),
            ],
        )
        self.assertEqual(len(result["entries"]), 4)
        self.assertEqual([m["numbers"] for m in result["mentions"]], [[1], [2], [3], [4]])
        for mention in result["mentions"]:
            self.assertEqual(body[mention["start"] : mention["end"]], mention["quote"])

    def test_ambiguous_same_name_year_and_wrong_coauthor(self):
        result = build_links(
            "doc",
            [
                page(
                    1,
                    "Clark et al., 2018; Howard and Wrong (2018).\nReferences\nChristopher Clark and Matt Gardner. 2018. First title.\nKevin Clark, Other Author, and Quoc Le. 2018. Second title.\nJeremy Howard and Sebastian Ruder. 2018. Third title.",
                )
            ],
        )
        self.assertEqual(len(result["entries"]), 3)
        self.assertEqual(result["mentions"], [])

    def test_venue_prefix_and_partial_authors_not_used(self):
        result = build_links(
            "doc",
            [
                page(
                    1,
                    "Radford et al., 2018. Uszkoreit (2016).\nReferences\nIn ACL.\nAlec Radford, Karthik Narasimhan, and Ilya Sutskever. 2018. First title.\nAnkur Parikh, Malformed ¨name, and\nJakob Uszkoreit. 2016. Second title.",
                )
            ],
        )
        self.assertEqual(result["entries"], [])


if __name__ == "__main__":
    unittest.main()
