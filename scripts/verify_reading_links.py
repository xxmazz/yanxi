"""只读核对官方 PDF 的引用、书目和显式编号目录导航；报告不公开全文摘录。"""

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
    for source in [
        *data["mentions"],
        *(s for e in data["entries"] for s in e["evidence"]),
        *data.get("outline", []),
    ]:
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
    if filename == "1706.03762v7.pdf":
        assert any(
            i["path"] == "3.2.1" and i["page"] == 4 and "Scaled Dot-Product" in i["title"]
            for i in data["outline"]
        )
        assert any(
            i["path"] == "5.2" and i["page"] == 7 and "Hardware" in i["title"]
            for i in data["outline"]
        )
    if filename == "N19-1423.pdf":
        assert any(
            i["path"] == "3.1" and i["page"] == 4 and "Pre-training BERT" in i["title"]
            for i in data["outline"]
        )
    for item in data["outline"]:
        assert 1 <= item["level"] <= 3
    lookup = {entry["number"] for entry in data["entries"]}
    assert all(set(m["numbers"]) <= lookup for m in data["mentions"])
    if filename == "1706.03762v7.pdf":
        assert lookup == set(range(1, 41))
        assert any(m["page"] == 2 and m["numbers"] == [13] for m in data["mentions"])
        entry = next(e for e in data["entries"] if e["number"] == 13)
        assert entry["evidence"][0]["page"] == 11
        assert "Hochreiter" in entry["evidence"][0]["quote"]
    if filename == "N19-1423.pdf":
        assert data["style"] == "author-year"
        expected = {
            "Peters et al., 2018a": "Deep contextualized word",
            "Radford et al., 2018": "Improving language",
            "Howard and Ruder, 2018": "Universal",
        }
        for label, title in expected.items():
            entry = next(e for e in data["entries"] if e["label"] == label)
            text = " ".join(s["quote"] for s in entry["evidence"]).replace("-\n", "")
            assert title.lower() in " ".join(text.split()).lower(), (label, text)
            assert any(entry["number"] in m["numbers"] and m["page"] == 1 for m in data["mentions"])
        assert all(not m["quote"].startswith("[") for m in data["mentions"])
    repeat = client.get(f"/api/documents/{doc_id}/reading-links").raise_for_status().json()
    assert data == repeat
    results.append(
        {
            "filename": filename,
            "document_sha256": doc_id,
            "fingerprint": data["fingerprint"],
            "entries": len(data["entries"]),
            "outline_items": len(data["outline"]),
            "mentions": len(data["mentions"]),
            "verified_offset_and_hash_intervals": count,
        }
    )
assert client.get("/api/documents/missing/reading-links").status_code == 404
report = {
    "verified_at": datetime.now(timezone.utc).isoformat(),
    "cases": results,
    "limitations": "核对目录、数字制与作者年份制候选原文坐标，不代表召回率。BERT 抽查三个首页引用的书目题名对应，其余候选仅核对位置。复杂姓名、OCR、上标仍未全面覆盖。无模型推理。",
}
args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n")
print(json.dumps(results, ensure_ascii=False))
