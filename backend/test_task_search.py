"""研究任务按当前主题选择候选，不把文献库相似度当作本次主题分数。"""

import importlib
import unittest
from copy import deepcopy
from unittest.mock import AsyncMock, patch

from backend.discovery import rank_topic_candidates


class TaskSearchTests(unittest.IsolatedAsyncioTestCase):
    def response(self):
        return {
            "query": "检索增强生成的自动评估",
            "search_query": "retrieval augmented generation automatic evaluation",
            "steps": ["将中文主题转换为英文检索词", "查询 arXiv 官方元数据"],
            "items": [
                {
                    "arxiv_id": "2005.11401v4",
                    "title": "Library-near candidate",
                    "abstract": "The current library is interested in generation.",
                    "related_document": {"similarity": 0.99},
                },
                {
                    "arxiv_id": "2309.15217v1",
                    "title": "Topic-near candidate",
                    "abstract": "Automatic evaluation of retrieval augmented generation.",
                    "related_document": {"similarity": 0.1},
                },
            ],
        }

    async def test_search_ranks_by_translated_topic_and_preserves_official_position(self):
        module = importlib.import_module("backend.app")
        response = self.response()
        calls = []

        def encode(texts, query=False):
            calls.append((texts, query))
            return [[1.0, 0.0]] if query else [[0.0, 1.0], [2.0, 0.0]]

        search = AsyncMock(return_value=response)
        with (
            patch.object(module, "search_papers", search),
            patch.object(module.models, "encoder", object()),
            patch.object(module.models, "encode", side_effect=encode),
        ):
            result = await module.task_search(response["query"])

        search.assert_awaited_once_with(q=response["query"], start=0, sort="relevance")
        self.assertEqual(calls[0], ([response["search_query"]], True))
        self.assertEqual(
            calls[1],
            (
                [
                    "Library-near candidate The current library is interested in generation.",
                    "Topic-near candidate Automatic evaluation of retrieval augmented generation.",
                ],
                False,
            ),
        )
        self.assertEqual(result["selection_method"], "e5_topic")
        self.assertEqual([item["official_rank"] for item in result["items"]], [2, 1])
        self.assertEqual([item["topic_similarity"] for item in result["items"]], [1.0, 0.0])
        self.assertEqual(result["items"][0]["related_document"]["similarity"], 0.1)
        self.assertIn("本次主题", result["steps"][-1])

    async def test_search_without_encoder_keeps_official_order(self):
        module = importlib.import_module("backend.app")
        response = self.response()
        originals = deepcopy(response["items"])
        with (
            patch.object(module, "search_papers", AsyncMock(return_value=response)),
            patch.object(module.models, "encoder", None),
            patch.object(module.models, "encode") as encode,
        ):
            result = await module.task_search(response["query"])
        encode.assert_not_called()
        self.assertEqual(result["selection_method"], "official_relevance")
        self.assertEqual(result["items"], originals)
        self.assertEqual(len(result["steps"]), 2)

    def test_cosine_ties_keep_official_order_without_mutating_candidates(self):
        items = [{"arxiv_id": identity} for identity in ["first", "second", "opposite"]]
        originals = deepcopy(items)
        result = rank_topic_candidates(items, [3.0, 0.0], [[2.0, 0.0], [5.0, 0.0], [-1.0, 0.0]])
        self.assertEqual([item["arxiv_id"] for item in result], ["first", "second", "opposite"])
        self.assertEqual([item["topic_similarity"] for item in result], [1.0, 1.0, -1.0])
        self.assertEqual(items, originals)

    def test_invalid_vectors_are_rejected_instead_of_silently_selecting(self):
        items = [{"arxiv_id": "candidate"}]
        cases = [
            ([1.0, 0.0], []),
            ([1.0, 0.0], [[1.0]]),
            ([0.0, 0.0], [[1.0, 0.0]]),
            ([], [[1.0, 0.0]]),
            ([1.0, 0.0], [[0.0, 0.0]]),
            ([float("nan"), 0.0], [[1.0, 0.0]]),
            ([1.0, 0.0], [[float("inf"), 0.0]]),
        ]
        for query, passages in cases:
            with self.subTest(query=query, passages=passages), self.assertRaises(ValueError):
                rank_topic_candidates(items, query, passages)
