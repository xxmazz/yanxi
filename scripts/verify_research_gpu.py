"""只读核对已完成的研究任务、本地模型来源覆盖和原文件；不建立任务或运行推理。"""

import argparse
import hashlib
import json
import re
from datetime import datetime, timezone
from pathlib import Path

import httpx

parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument("--base-url", default="http://127.0.0.1:18474")
parser.add_argument("--task-id", action="append", required=True)
parser.add_argument("--output", type=Path, required=True)
args = parser.parse_args()
client = httpx.Client(base_url=args.base_url, timeout=120, trust_env=False)


def get(path):
    return client.get(path).raise_for_status().json()


def sha(content):
    return hashlib.sha256(content).hexdigest()


health = get("/api/health")
assert health["model_ready"] and health["embedding_ready"]
available = {doc["id"]: doc for doc in get("/api/documents")}
cases = []
for task_id in args.task_id:
    task = get(f"/api/research/tasks/{task_id}")
    assert task["status"] == "complete", task.get("error") or task["status"]
    ids = {item["document_id"] for item in task["selected"]}
    assert len(ids) == len(task["selected"]) == len(task["results"])
    assert {item["document_id"] for item in task["results"]} == ids
    summary = task["summary"]
    assert summary["insufficient_evidence"] is False
    assert set(summary["covered_documents"]) == ids
    assert {item["document_id"] for item in summary["citations"]} == ids
    assert set(re.findall(r"\[(E\d+)\]", summary["answer"])) == {
        item["id"] for item in summary["citations"]
    }
    graph = (
        client.post("/api/graph/build", json={"document_ids": list(ids)}).raise_for_status().json()
    )
    assert graph["fingerprint"] == summary["fingerprint"]
    results = []
    for item in task["selected"]:
        doc_id = item["document_id"]
        doc = available[doc_id]
        assert doc["status"] == "ready" and doc["is_active"] and not doc["trashed_at"]
        assert sha(client.get(f"/api/documents/{doc_id}/pdf").raise_for_status().content) == doc_id
        assert any(
            s["arxiv_id"] == item["arxiv_id"] for s in get(f"/api/documents/{doc_id}/sources")
        )
        results.append(
            {
                key: item.get(key)
                for key in ["arxiv_id", "document_id", "official_rank", "topic_similarity"]
            }
            | {"title": doc["title"], "pages": doc["pages"]}
        )
    evidence = []
    for item in summary["citations"]:
        text = get(f"/api/documents/{item['document_id']}/pages/{item['page']}")["text"]
        start = text.find(item["quote"])
        assert start >= 0 and len(item["quote"]) >= 15
        evidence.append(
            {key: item[key] for key in ["id", "document_id", "page"]}
            | {
                "start": start,
                "end": start + len(item["quote"]),
                "page_sha256": sha(text.encode()),
                "quote_sha256": sha(item["quote"].encode()),
            }
        )
    if task.get("selection_method") == "e5_topic":
        candidates = task["candidates"]
        assert 1 <= len(candidates) <= 8
        scores = [item["topic_similarity"] for item in candidates]
        assert all(a >= b for a, b in zip(scores, scores[1:]))
    cases.append(
        {
            "task_id": task_id,
            "query": task["query"],
            "search_query": task.get("search_query"),
            "selection_method": task.get("selection_method", "official_relevance"),
            "attempt": task["attempt"],
            "status": task["status"],
            "search_steps": task.get("search_steps", []),
            "results": results,
            "answer": summary["answer"],
            "fingerprint": summary["fingerprint"],
            "verified_sources": evidence,
        }
    )
report = {
    "verified_at": datetime.now(timezone.utc).isoformat(),
    "health": health,
    "cases": cases,
    "limitations": "核对真实任务、PDF身份、摘要来源位置、范围覆盖及快照一致性；结构检查不等于语义蕴含判断，中文结论另行人工核对。只概括所选摘要，未做全文综述；E5仅重排官方首批最多八篇候选，不代表全库召回率、论文质量或主题相关概率。",
}
args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n")
print([(case["query"], len(case["results"]), case["attempt"]) for case in cases])
