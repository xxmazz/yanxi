"""真实服务检查：上传、等待索引、问答，并用返回的 PDF 验证引用位置。"""

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
parser.add_argument("--pdf", required=True)
parser.add_argument("--question", required=True)
parser.add_argument("--expect", action="append", default=[])
args = parser.parse_args()
with httpx.Client(base_url=args.base_url, timeout=180) as client:
    with open(args.pdf, "rb") as stream:
        response = client.post("/api/documents", files={"file": stream})
    response.raise_for_status()
    doc_id = response.json()["id"]
    for _ in range(180):
        docs = client.get("/api/documents").raise_for_status().json()
        doc = next(d for d in docs if d["id"] == doc_id)
        if doc["status"] == "error":
            raise RuntimeError(doc["error"])
        if doc["status"] == "ready":
            break
        time.sleep(1)
    else:
        raise TimeoutError("文献索引超时")
    start = time.monotonic()
    result = client.post("/api/chat", json={"question": args.question, "document_ids": [doc_id]})
    result.raise_for_status()
    data = result.json()
    if data["insufficient_evidence"] or not data["citations"]:
        raise AssertionError("没有通过来源核验的模型回答")
    for expected in args.expect:
        if expected not in data["answer"]:
            raise AssertionError(f"回答缺少预期内容：{expected}")
    original = client.get(f"/api/documents/{doc_id}/pdf").raise_for_status().content
    if hashlib.sha256(original).hexdigest() != doc_id:
        raise AssertionError("原文下载内容与文献 ID 不一致")
    reader = PdfReader(io.BytesIO(original))

    def normalize(text):
        return re.sub(r"\s+", "", text).lower()

    for reference in data["citations"]:
        if reference["document_id"] != doc_id:
            raise AssertionError("出现范围之外的文献")
        page = reader.pages[reference["page"] - 1].extract_text()
        if normalize(reference["quote"]) not in normalize(page):
            raise AssertionError("引用摘录不在对应原文页码中")
    print(
        json.dumps(
            {
                "seconds": round(time.monotonic() - start, 2),
                "pages": len(reader.pages),
                "question": args.question,
                "result": data,
            },
            ensure_ascii=False,
            indent=2,
        )
    )
