"""只读核对官方 PDF 的数字制书目导航；报告不公开全文摘录。"""

import argparse
import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path

import httpx

parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument("--base-url", default="http://127.0.0.1:18474")
parser.add_argument("--fixture-dir", type=Path, required=True)
parser.add_argument("--output", type=Path, required=True)
args = parser.parse_args()
client = httpx.Client(base_url=args.base_url, trust_env=False, timeout=120)
results = []
for filename in ["1706.03762v7.pdf", "1706.03762v1_img.pdf", "N19-1423.pdf"]:
    doc_id = hashlib.sha256((args.fixture_dir / filename).read_bytes()).hexdigest()
    data = client.get(f"/api/documents/{doc_id}/reading-links").raise_for_status().json()
    pages = {}
    count = 0
    for source in [*data["mentions"], *(s for e in data["entries"] for s in e["evidence"])]:
        number = source["page"]
        if number not in pages:
            pages[number] = (
                client.get(f"/api/documents/{doc_id}/pages/{number}")
                .raise_for_status()
                .json()["text"]
            )
        text = pages[number]
        assert text[source["start"] : source["end"]] == source["quote"]
        assert hashlib.sha256(text.encode()).hexdigest() == source["page_sha256"]
        count += 1
    lookup = {entry["number"] for entry in data["entries"]}
    assert all(set(m["numbers"]) <= lookup for m in data["mentions"])
    if filename == "1706.03762v7.pdf":
        assert lookup == set(range(1, 41))
        assert any(m["page"] == 2 and m["numbers"] == [13] for m in data["mentions"])
        entry = next(e for e in data["entries"] if e["number"] == 13)
        assert entry["evidence"][0]["page"] == 11
        assert "Hochreiter" in entry["evidence"][0]["quote"]
    if filename == "N19-1423.pdf":
        assert not data["entries"] and not data["mentions"], "作者年份制不可猜成数字编号"
    repeat = client.get(f"/api/documents/{doc_id}/reading-links").raise_for_status().json()
    assert data == repeat
    results.append(
        {
            "filename": filename,
            "document_sha256": doc_id,
            "fingerprint": data["fingerprint"],
            "entries": len(data["entries"]),
            "mentions": len(data["mentions"]),
            "verified_offset_and_hash_intervals": count,
        }
    )
assert client.get("/api/documents/missing/reading-links").status_code == 404
report = {
    "verified_at": datetime.now(timezone.utc).isoformat(),
    "cases": results,
    "limitations": "仅核对数字制候选及原文坐标，不代表引用识别召回率。OCR 错字、作者年份制、上标和复杂排版未全面覆盖。浏览器另行实测 [13] 第2页→第11页→返回第2页及原文高亮。无模型推理。",
}
args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n")
print(json.dumps(results, ensure_ascii=False))
