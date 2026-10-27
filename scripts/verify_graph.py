"""核对真实服务的图谱、引用页坐标、版本合并及本地模型综述。"""

import argparse
import hashlib
import json
import re
import time
from datetime import datetime, timezone
from pathlib import Path

import httpx

parser = argparse.ArgumentParser()
parser.add_argument("--base-url", default="http://127.0.0.1:18474")
parser.add_argument("--review", action="store_true")
parser.add_argument("--output", type=Path)
args = parser.parse_args()
client = httpx.Client(base_url=args.base_url, trust_env=False, timeout=240)


def request(method, path, **kwargs):
    return client.request(method, path, **kwargs).raise_for_status().json()


start = time.monotonic()
graph = request("POST", "/api/graph/build", json={})
seconds = round(time.monotonic() - start, 3)
assert len(graph["nodes"]) >= 3, "请先导入至少三篇真实论文"
nodes = {n["id"]: n for n in graph["nodes"]}
assert abs(sum(n["pagerank"] for n in nodes.values()) - 1) < 1e-6
assert graph["stats"]["documents"] == len(graph["document_ids"])
page_cache = {}


def raw_page(doc_id, number):
    key = (doc_id, number)
    if key not in page_cache:
        page_cache[key] = request("GET", f"/api/documents/{doc_id}/pages/{number}")
    return page_cache[key]["text"]


def verify_source(source):
    text = raw_page(source["document_id"], source["page"])
    assert text[source["start"] : source["end"]] == source["quote"]
    assert hashlib.sha256(text.encode()).hexdigest() == source["page_sha256"]


for edge in graph["edges"]:
    assert edge["source"] != edge["target"]
    assert edge["source"] in nodes and edge["target"] in nodes
    assert edge["evidence"]
    for source in edge["evidence"]:
        verify_source(source)
        assert source["document_id"] == nodes[edge["source"]]["document_id"]
        assert source["page"] in nodes[edge["source"]]["reference_pages"]
for node in nodes.values():
    assert node["in_degree"] == sum(e["target"] == node["id"] for e in graph["edges"])
    assert node["out_degree"] == sum(e["source"] == node["id"] for e in graph["edges"])
    for source in [node["abstract_evidence"], *node["future_work"]]:
        if source:
            verify_source(source)
repeat = request("POST", "/api/graph/build", json={"document_ids": graph["document_ids"]})
assert repeat["fingerprint"] == graph["fingerprint"]
assert repeat["nodes"] == graph["nodes"] and repeat["edges"] == graph["edges"]
stale = client.post(
    "/api/graph/review", json={"document_ids": graph["document_ids"], "fingerprint": "0" * 64}
)
assert stale.status_code == 409, stale.text
single = request("POST", "/api/graph/build", json={"document_ids": [graph["document_ids"][0]]})
assert single["stats"]["works"] == 1 and single["edges"] == []
review = None
if args.review:
    start = time.monotonic()
    review = request(
        "POST",
        "/api/graph/review",
        json={"document_ids": graph["document_ids"], "fingerprint": graph["fingerprint"]},
    )
    review["seconds"] = round(time.monotonic() - start, 2)
    assert review["fingerprint"] == graph["fingerprint"]
    assert not review["insufficient_evidence"], review
    assert review["citations"]

    def normalize(text):
        return re.sub(r"\s+", "", text).lower()

    for source in review["citations"]:
        assert source["document_id"] in graph["document_ids"]
        assert normalize(source["quote"]) in normalize(
            raw_page(source["document_id"], source["page"])
        )


def public_record(value):
    """公开验收记录只保留摘录校验值，完整原文留在文献库。"""
    if isinstance(value, list):
        return [public_record(item) for item in value]
    if isinstance(value, dict):
        result = {key: public_record(item) for key, item in value.items() if key != "quote"}
        if "quote" in value:
            result["quote_sha256"] = hashlib.sha256(value["quote"].encode()).hexdigest()
            result["quote_characters"] = len(value["quote"])
        return result
    return value


result = {
    "verified_at": datetime.now(timezone.utc).isoformat(),
    "model": request("GET", "/api/health")["model"],
    "stats": graph["stats"],
    "build_seconds": seconds,
    "fingerprint": graph["fingerprint"],
    "source_offsets_and_hashes_verified": True,
    "degree_and_pagerank_verified": True,
    "scope_and_stale_rejection_verified": True,
    "graph": public_record(graph),
    "review": public_record(review),
    "limitations": "真实样本规模以 stats 为准；20 篇拓扑使用构造数据测试。此记录不验证所有论文的引用召回率、综述语义完全正确性或未来方向新颖性。",
}
content = json.dumps(result, ensure_ascii=False, indent=2) + "\n"
if args.output:
    args.output.write_text(content)
print(content if not args.output else f"Verified: {graph['stats']}; output: {args.output}")
