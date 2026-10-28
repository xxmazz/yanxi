"""只读核验官方 20 篇 PDF 的候选引用、原文证据与局部排名。"""

import argparse
import hashlib
import json
import re
import time
from datetime import datetime, timezone
from pathlib import Path

import httpx

parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument("--base-url", default="http://127.0.0.1:18474")
parser.add_argument("--cases", type=Path, default=Path("docs/GRAPH20_CASES.json"))
parser.add_argument("--fixture-dir", type=Path, required=True)
parser.add_argument("--review", action="store_true")
parser.add_argument("--output", type=Path, required=True)
args = parser.parse_args()
cases = json.loads(args.cases.read_text())
client = httpx.Client(base_url=args.base_url, trust_env=False, timeout=300)


def request(method, path, **kwargs):
    return client.request(method, path, **kwargs).raise_for_status().json()


def sha(data):
    return hashlib.sha256(data).hexdigest()


documents = cases["documents"]
assert len(documents) == 20 and sum(d["pages"] for d in documents) == 816
available = {d["id"]: d for d in request("GET", "/api/documents")}
for doc in documents:
    doc_id = doc["sha256"]
    assert sha((args.fixture_dir / doc["filename"]).read_bytes()) == doc_id
    pdf = client.get(f"/api/documents/{doc_id}/pdf").raise_for_status()
    assert sha(pdf.content) == doc_id
    saved = available[doc_id]
    assert saved["status"] == "ready" and saved["is_active"]
    assert not saved["trashed_at"] and saved["pages"] == doc["pages"]

ids = [d["sha256"] for d in documents]
start = time.monotonic()
graph = request("POST", "/api/graph/build", json={"document_ids": ids})
seconds = round(time.monotonic() - start, 3)
assert graph["stats"]["works"] == 20 and graph["stats"]["documents"] == 20
assert set(graph["document_ids"]) == set(ids)
nodes = {n["id"]: n for n in graph["nodes"]}
by_doc = {n["document_id"]: n for n in nodes.values()}
assert len(nodes) == len(by_doc) == 20
for doc in documents:
    assert by_doc[doc["sha256"]]["title"] == doc["title"]


def pair(source, target):
    return by_doc[ids[source]]["id"], by_doc[ids[target]]["id"]


expected = {pair(s, t) for s, t in cases["positive_pairs"]}
actual = {(e["source"], e["target"]) for e in graph["edges"]}
assert len(actual) == len(graph["edges"]), "同作品重复边"
assert expected == actual, {
    "missing": sorted(expected - actual),
    "extra": sorted(actual - expected),
}
for case in cases["negative_pairs"]:
    assert pair(case["source"], case["target"]) not in actual

page_cache = {}
evidence_count = 0


def raw_page(doc_id, page):
    key = doc_id, page
    if key not in page_cache:
        page_cache[key] = request("GET", f"/api/documents/{doc_id}/pages/{page}")["text"]
    return page_cache[key]


def verify_source(source):
    global evidence_count
    text = raw_page(source["document_id"], source["page"])
    assert 0 <= source["start"] < source["end"] <= len(text)
    assert text[source["start"] : source["end"]] == source["quote"]
    assert sha(text.encode()) == source["page_sha256"]
    evidence_count += 1


manifest = {d["sha256"]: d for d in documents}
for edge in graph["edges"]:
    assert edge["source"] != edge["target"] and edge["evidence"]
    for source in edge["evidence"]:
        verify_source(source)
        node = nodes[edge["source"]]
        assert source["document_id"] == node["document_id"]
        assert source["page"] in node["reference_pages"]
        low, high = manifest[source["document_id"]]["reference_page_range"]
        assert low <= source["page"] <= high
for node in nodes.values():
    assert node["in_degree"] == sum(t == node["id"] for s, t in actual)
    assert node["out_degree"] == sum(s == node["id"] for s, t in actual)
    for source in [node["abstract_evidence"], *node["future_work"]]:
        if source:
            verify_source(source)

# 独立幂迭代复算 PageRank，不调用后端构图函数或 NetworkX。
rank = {key: 1 / len(nodes) for key in nodes}
for _ in range(1000):
    dangling = sum(rank[key] for key, n in nodes.items() if not n["out_degree"])
    updated = {key: (0.15 + 0.85 * dangling) / len(nodes) for key in nodes}
    for source, target in actual:
        updated[target] += 0.85 * rank[source] / nodes[source]["out_degree"]
    error = sum(abs(updated[key] - rank[key]) for key in nodes)
    rank = updated
    if error < 1e-12:
        break
assert abs(sum(n["pagerank"] for n in nodes.values()) - 1) < 1e-6
assert max(abs(rank[key] - n["pagerank"]) for key, n in nodes.items()) < 1e-4
repeat = request("POST", "/api/graph/build", json={"document_ids": ids})
for key in ["fingerprint", "nodes", "edges", "stats"]:
    assert graph[key] == repeat[key]
stale = client.post("/api/graph/review", json={"document_ids": ids, "fingerprint": "0" * 64})
assert stale.status_code == 409
single = request("POST", "/api/graph/build", json={"document_ids": [ids[0]]})
assert single["stats"]["works"] == 1 and not single["edges"]
missing = client.post("/api/graph/build", json={"document_ids": ["0" * 64]})
assert missing.status_code == 409

review = None
if args.review:
    start = time.monotonic()
    review = request(
        "POST", "/api/graph/review", json={"document_ids": ids, "fingerprint": graph["fingerprint"]}
    )
    review["seconds"] = round(time.monotonic() - start, 2)
    assert not review["insufficient_evidence"], review
    assert review["fingerprint"] == graph["fingerprint"] and review["citations"]
    for citation in review["citations"]:
        assert citation["document_id"] in ids
        quote = re.sub(r"\s+", "", citation["quote"]).lower()
        text = re.sub(r"\s+", "", raw_page(citation["document_id"], citation["page"])).lower()
        assert quote in text


def public_record(value):
    if isinstance(value, list):
        return [public_record(item) for item in value]
    if isinstance(value, dict):
        result = {key: public_record(item) for key, item in value.items() if key != "quote"}
        if "quote" in value:
            result["quote_sha256"] = sha(value["quote"].encode())
            result["quote_characters"] = len(value["quote"])
        return result
    return value


result = {
    "verified_at": datetime.now(timezone.utc).isoformat(),
    "model": request("GET", "/api/health")["model"],
    "cases_sha256": sha(args.cases.read_bytes()),
    "pdf_files_and_download_hashes_verified": len(documents),
    "physical_pages": sum(d["pages"] for d in documents),
    "confirmed_positive_pairs_covered": len(expected),
    "confusable_negative_pairs_rejected": len(cases["negative_pairs"]),
    "source_offsets_and_hashes_verified": evidence_count,
    "independent_pagerank_and_degrees_verified": True,
    "determinism_scope_and_stale_rejection_verified": True,
    "build_seconds": seconds,
    "graph": public_record(graph),
    "review": public_record(review),
    "limitations": cases["method"]
    + " 综述最多读取六篇摘要；引用存在不等于语义完全正确，局部排名不等于全球被引量。",
}
args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n")
print(
    f"Verified: {graph['stats']}; positive={len(expected)}, negative={len(cases['negative_pairs'])}; {args.output}"
)
