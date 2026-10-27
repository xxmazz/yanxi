"""真实服务回归：中文主题、编号导入、解析、来源、重复入库及带页码问答。"""

import argparse
import hashlib
import io
import json
import re
import time

import httpx
from pypdf import PdfReader

parser = argparse.ArgumentParser()
parser.add_argument("--base-url", default="http://127.0.0.1:18474")
args = parser.parse_args()


def normalize(text):
    return re.sub(r"\s+", "", text).lower()


with httpx.Client(base_url=args.base_url, timeout=180, trust_env=False) as client:
    start = time.monotonic()
    search = (
        client.get("/api/discovery/search", params={"q": "检索增强生成", "sort": "library"})
        .raise_for_status()
        .json()
    )
    assert search["search_query"] == "retrieval augmented generation"
    assert search["items"] and all(
        item["url"].startswith("https://arxiv.org/abs/") for item in search["items"]
    )
    assert all(item.get("related_document") for item in search["items"])
    similarities = [item["related_document"]["similarity"] for item in search["items"]]
    assert similarities == sorted(similarities, reverse=True)
    search_seconds = round(time.monotonic() - start, 2)
    job = (
        client.post("/api/discovery/import", json={"arxiv_id": "2005.11401v4"})
        .raise_for_status()
        .json()
    )
    for _ in range(180):
        jobs = client.get("/api/discovery/jobs").raise_for_status().json()
        job = next(value for value in jobs if value["id"] == job["id"])
        if job["status"] == "error":
            raise RuntimeError(job["error"])
        if job["status"] == "complete":
            break
        time.sleep(1)
    else:
        raise TimeoutError("论文下载超时")
    for _ in range(180):
        document = next(
            d
            for d in client.get("/api/documents").raise_for_status().json()
            if d["id"] == job["document_id"]
        )
        if document["status"] == "ready":
            break
        if document["status"] in ("error", "review"):
            raise RuntimeError("文献需要处理：" + document["status"])
        time.sleep(1)
    else:
        raise TimeoutError("文献解析超时")
    repeated = (
        client.post("/api/discovery/import", json={"arxiv_id": "2005.11401v4"})
        .raise_for_status()
        .json()
    )
    for _ in range(60):
        repeated = next(
            j
            for j in client.get("/api/discovery/jobs").raise_for_status().json()
            if j["id"] == repeated["id"]
        )
        if repeated["status"] == "complete":
            assert repeated["document_id"] == document["id"]
            break
        if repeated["status"] == "error":
            raise RuntimeError(repeated["error"])
        time.sleep(1)
    else:
        raise TimeoutError("重复导入未完成")
    sources = client.get(f"/api/documents/{document['id']}/sources").raise_for_status().json()
    assert sources[0]["arxiv_id"] == "2005.11401v4"
    assert "Patrick Lewis" in sources[0]["authors"]
    content = client.get(f"/api/documents/{document['id']}/pdf").raise_for_status().content
    assert hashlib.sha256(content).hexdigest() == document["id"]
    reader = PdfReader(io.BytesIO(content))
    start = time.monotonic()
    answer = (
        client.post(
            "/api/chat",
            json={"question": "RAG 的非参数记忆使用什么数据源？", "document_ids": [document["id"]]},
        )
        .raise_for_status()
        .json()
    )
    assert not answer["insufficient_evidence"] and answer["citations"]
    assert "维基百科" in answer["answer"] or "wikipedia" in answer["answer"].lower()
    for citation in answer["citations"]:
        assert citation["document_id"] == document["id"]
        assert normalize(citation["quote"]) in normalize(
            reader.pages[citation["page"] - 1].extract_text()
        )
    print(
        json.dumps(
            {
                "search_seconds": search_seconds,
                "query": search["query"],
                "search_query": search["search_query"],
                "returned": len(search["items"]),
                "total": search["total"],
                "library_ranking_verified": True,
                "sample_results": [
                    {key: item[key] for key in ("arxiv_id", "title", "url", "related_document")}
                    for item in search["items"][:3]
                ],
                "document": {
                    key: document[key] for key in ("id", "title", "pages", "status", "version")
                },
                "source": {
                    key: sources[0][key] for key in ("arxiv_id", "url", "authors", "published")
                },
                "repeat_import_same_document": True,
                "pdf_sha256_verified": True,
                "chat_seconds": round(time.monotonic() - start, 2),
                "answer": answer,
                "citations_verified_in_original_pdf": True,
            },
            ensure_ascii=False,
            indent=2,
        )
    )
