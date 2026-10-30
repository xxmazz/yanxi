import importlib
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from fastapi.testclient import TestClient

from backend.core import Store
from backend.discovery import DiscoveryError
from backend.following import Following, reference_candidates
from backend.reference_resolution import ReferenceResolver, fields, verify


def nav():
    return {
        "fingerprint": "f" * 64,
        "entries": [
            {
                "label": "[11]",
                "evidence": [
                    {
                        "page": 10,
                        "quote": "[11] Kaiming He, Xiangyu Zhang, Shaoqing Ren, and Jian Sun. Deep residual learning for im-\nage recognition. In CVPR, pages 770–778, 2016.",
                    }
                ],
            },
            {
                "label": "[20]",
                "evidence": [
                    {
                        "page": 11,
                        "quote": "[20] Diederik Kingma and Jimmy Ba. Adam: A method for stochastic optimization. In ICLR, 2015.",
                    }
                ],
            },
            {
                "label": "[2]",
                "evidence": [
                    {
                        "page": 10,
                        "quote": "[2] Dzmitry Bahdanau and Kyunghyun Cho. Neural translation. CoRR, abs/1409.0473, 2014.",
                    }
                ],
            },
            {
                "label": "[3]",
                "evidence": [
                    {"page": 10, "quote": "[3] Same version https://arxiv.org/abs/1409.0473v7"}
                ],
            },
        ],
    }


def paper(**changes):
    return {
        "arxiv_id": "1512.03385v1",
        "base_id": "1512.03385",
        "title": "Deep Residual Learning for Image Recognition",
        "authors": ["Kaiming He", "Xiangyu Zhang", "Shaoqing Ren", "Jian Sun"],
        "published": "2015-12-10T00:00:00Z",
        "url": "https://arxiv.org/abs/1512.03385v1",
        **changes,
    }


class ReferenceResolutionTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.store = Store(Path(self.temp.name))
        self.service = ReferenceResolver(self.store)

    def tearDown(self):
        self.temp.cleanup()

    def resolve(self, items, **changes):
        discovery = Mock()
        discovery.request.return_value = {
            "items": items,
            "cached": False,
            "fetched_at": "2026-10-30",
            **changes,
        }
        result = self.service.resolve("source", nav(), "[11]", discovery)
        query = discovery.request.call_args.args[0]["search_query"]
        self.assertIn('ti:"residual"', query)
        self.assertNotIn("all:", query)
        return result

    def test_title_author_year_fields_linebreaks_and_author_initials(self):
        reference = fields(nav()["entries"][0])
        self.assertEqual(reference["title"], "Deep residual learning for image recognition")
        self.assertEqual(reference["year"], 2016)
        self.assertEqual(reference["pages"], [10])
        other = fields(
            {
                "label": "Smith, 2020",
                "evidence": [
                    {
                        "page": 3,
                        "quote": "John A. Smith and Anne B. Jones. 2020. Can memory replace attention? In Proceedings.",
                    }
                ],
            }
        )
        self.assertEqual(other["title"], "Can memory replace attention?")
        self.assertEqual(other["authors"], "John A. Smith and Anne B. Jones")
        self.assertEqual(
            fields({"label": "[1]", "evidence": [{"page": 1, "quote": "No structured reference"}]})[
                "title"
            ],
            "",
        )

    def test_exact_match_requires_title_authors_year_not_just_similar_words(self):
        reference = fields(nav()["entries"][0])
        self.assertTrue(verify(reference, paper())["verified"])
        for changes in [
            {"title": "Deep Residual Learning for Image Recognition: A Survey"},
            {"title": "Residual Learning for Image Recognition"},
            {"authors": ["Other He", "Xiangyu Zhang"]},
            {"published": "2025-01-01"},
            {"published": "invalid"},
            {"authors": []},
        ]:
            self.assertFalse(verify(reference, paper(**changes))["verified"], changes)
        self.assertFalse(verify({**reference, "year": None}, paper())["verified"])

    def test_unique_resolution_persists_and_only_current_source_can_download(self):
        result = self.resolve(
            [paper(), paper(base_id="other", arxiv_id="2401.00001", title="A survey")]
        )
        self.assertEqual(result["status"], "matched")
        self.service.save("source", nav()["fingerprint"], result)
        restored = ReferenceResolver(self.store)
        candidates = restored.candidates("source", nav())
        self.assertEqual(len(candidates["items"]), 2)  # explicit CoRR plus title verified
        resolved = next(i for i in candidates["items"] if i["arxiv_id"] == "1512.03385v1")
        self.assertEqual(resolved["match_method"], "title-authors-year")
        self.assertEqual(resolved["match"]["published_year"], 2015)
        self.assertEqual(candidates["unresolved"], 1)
        self.assertEqual(candidates["resolved_by_title"], 1)
        changed = restored.candidates("source", {**nav(), "fingerprint": "a" * 64})
        self.assertNotIn("1512.03385v1", [i["arxiv_id"] for i in changed["items"]])
        self.assertNotIn(
            "1512.03385v1", [i["arxiv_id"] for i in restored.candidates("another", nav())["items"]]
        )

    def test_ambiguous_works_no_match_and_stale_are_never_selected(self):
        result = self.resolve([paper(), paper(base_id="1601.00001", arxiv_id="1601.00001v1")])
        self.assertEqual(result["status"], "ambiguous")
        self.assertIsNone(result["selected"])
        self.assertEqual(self.resolve([paper(title="Unrelated title")])["status"], "no_match")
        with self.assertRaises(DiscoveryError):
            self.resolve([paper()], stale=True)
        self.assertEqual(self.service.saved("source", nav()["fingerprint"]), {})
        # Multiple versions of one identity are not multiple works.
        self.assertEqual(
            self.resolve([paper(), paper(arxiv_id="1512.03385v2")])["status"], "matched"
        )

    def test_explicit_corr_ids_only_and_version_dedup(self):
        result = reference_candidates(nav())
        self.assertEqual(len(result["items"]), 1)
        self.assertEqual(result["items"][0]["arxiv_id"], "1409.0473")
        self.assertEqual(result["unresolved"], 2)
        self.assertEqual(result["identified_references"], ["[2]", "[3]"])
        clean = {
            "fingerprint": "f" * 64,
            "entries": [
                {
                    "label": "[1]",
                    "evidence": [
                        {"page": 1, "quote": "Reference pages 1409.0473, abs/1601.00001, year2016"}
                    ],
                }
            ],
        }
        self.assertEqual(reference_candidates(clean)["items"], [])

    def test_routes_reject_forged_labels_scope_changes_and_use_saved_match_for_queue(self):
        module = importlib.import_module("backend.app")
        discovery = Mock()
        discovery.request.return_value = {"items": [paper()], "cached": False}
        following = Following(self.store)
        with (
            patch.object(module, "reference_resolver", self.service),
            patch.object(module, "store", self.store),
            patch.object(module, "discovery", discovery),
            patch.object(module, "following", following),
            patch.object(module, "navigation", return_value=nav()),
            patch.object(following, "start_batch") as start,
        ):
            client = TestClient(module.app)
            base = "/api/documents/source"
            request = {"fingerprint": "f" * 64, "reference": "[11]"}
            self.assertEqual(
                client.post(
                    base + "/reference-resolutions", json={**request, "fingerprint": "0" * 64}
                ).status_code,
                409,
            )
            self.assertEqual(
                client.post(
                    base + "/reference-resolutions", json={**request, "reference": "[999]"}
                ).status_code,
                409,
            )
            discovery.request.assert_not_called()
            self.assertEqual(
                client.post(
                    base + "/reference-batches",
                    json={"fingerprint": "f" * 64, "arxiv_ids": ["1512.03385v1"]},
                ).status_code,
                409,
            )
            response = client.post(base + "/reference-resolutions", json=request)
            self.assertEqual(response.status_code, 200)
            self.assertEqual(response.json()["status"], "matched")
            result = client.post(
                base + "/reference-batches",
                json={"fingerprint": "f" * 64, "arxiv_ids": ["1512.03385v1"]},
            )
            self.assertEqual(result.status_code, 202)
            self.assertEqual(result.json()["items"][0]["match_method"], "title-authors-year")
            self.assertTrue(result.json()["items"][0]["match"]["verified"])
            start.assert_called_once()
            self.assertEqual(
                client.post(
                    "/api/documents/other/reference-batches",
                    json={"fingerprint": "f" * 64, "arxiv_ids": ["1512.03385v1"]},
                ).status_code,
                409,
            )
            with patch.object(
                module, "navigation", side_effect=[nav(), {**nav(), "fingerprint": "c" * 64}]
            ):
                self.assertEqual(
                    client.post(
                        base + "/reference-resolutions", json={**request, "reference": "[20]"}
                    ).status_code,
                    409,
                )
            self.assertNotIn("[20]", self.service.saved("source", "f" * 64))
