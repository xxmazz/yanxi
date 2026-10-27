"""检查已有版本决策及真实扫描件问答，不上传文件、不改变版本决策。"""

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
parser.add_argument("--older-id", required=True)
parser.add_argument("--newer-id", required=True)
parser.add_argument("--scan-id", required=True)
args = parser.parse_args()


def normalized(text):
    return re.sub(r"\s+", "", text).lower()


with httpx.Client(base_url=args.base_url, timeout=180, trust_env=False) as client:
    all_docs = client.get("/api/documents?include_archived=true").raise_for_status().json()
    by_id = {d["id"]: d for d in all_docs}
    old, new, scan = [by_id[i] for i in (args.older_id, args.newer_id, args.scan_id)]
    assert old["version"] == 1 and new["version"] == 7
    assert old["is_active"] == 0 and new["is_active"] == 1
    assert scan["status"] == "ready" and scan["ocr_pages"] == scan["pages"] == 15
    for doc in (old, scan):
        content = client.get(f"/api/documents/{doc['id']}/pdf").raise_for_status().content
        assert hashlib.sha256(content).hexdigest() == doc["id"]
        if doc is scan:
            assert all(
                not (p.extract_text() or "").strip() for p in PdfReader(io.BytesIO(content)).pages
            )
    excluded = (
        client.post("/api/chat", json={"question": "模型维度？", "document_ids": [old["id"]]})
        .raise_for_status()
        .json()
    )
    assert excluded["insufficient_evidence"] and not excluded["citations"]
    page = client.get(f"/api/documents/{scan['id']}/pages/5").raise_for_status().json()
    assert page["method"] == "ocr" and "2048" in page["text"] and page["blocks"]
    assert all(len(b["bbox"]) == 4 and 0 <= b["confidence"] <= 1 for b in page["blocks"])
    start = time.monotonic()
    answer = (
        client.post(
            "/api/chat",
            json={
                "question": "Position-wise Feed-Forward Networks 的 inner-layer dimension 是多少？",
                "document_ids": [scan["id"]],
            },
        )
        .raise_for_status()
        .json()
    )
    assert not answer["insufficient_evidence"] and "2048" in answer["answer"]
    assert answer["citations"]
    for citation in answer["citations"]:
        assert citation["document_id"] == scan["id"]
        source = (
            client.get(f"/api/documents/{scan['id']}/pages/{citation['page']}")
            .raise_for_status()
            .json()
        )
        assert source["method"] == "ocr"
        assert normalized(citation["quote"]) in normalized(source["text"])
    print(
        json.dumps(
            {
                "fixture": "Attention v1 经 Poppler/Pillow 自行栅格化的 15 页无文本层 PDF，非官网 img 测试文件",
                "native_versions": {
                    "old_version": old["version"],
                    "new_version": new["version"],
                    "older_excluded": True,
                    "source_retained": True,
                },
                "scan": {
                    "id": scan["id"],
                    "pages": scan["pages"],
                    "ocr_pages": scan["ocr_pages"],
                    "page5_blocks": len(page["blocks"]),
                    "no_native_text": True,
                },
                "chat_seconds": round(time.monotonic() - start, 2),
                "answer": answer,
                "ocr_quote_page_verified": True,
            },
            ensure_ascii=False,
            indent=2,
        )
    )
