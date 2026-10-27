"""在真实本地模型上检查研究计划、书目、证据及架构图脚本。"""

import argparse
import hashlib
import json
import time
from datetime import datetime, timezone
from pathlib import Path

import httpx

parser = argparse.ArgumentParser()
parser.add_argument("--base-url", default="http://127.0.0.1:18474")
parser.add_argument("--document-id", action="append", dest="document_ids")
parser.add_argument(
    "--idea",
    default="面向科研文献的可验证问答与证据溯源：结合本地检索、引用核验和文献评估，设计研究方法与可复现实验。",
)
parser.add_argument("--output", type=Path)
args = parser.parse_args()
client = httpx.Client(base_url=args.base_url, trust_env=False, timeout=180)


def request(method, path, **kwargs):
    return client.request(method, path, **kwargs).raise_for_status().json()


def public_record(value):
    if isinstance(value, list):
        return [public_record(item) for item in value]
    if isinstance(value, dict):
        result = {key: public_record(item) for key, item in value.items() if key != "quote"}
        if "quote" in value:
            result["quote_sha256"] = hashlib.sha256(value["quote"].encode()).hexdigest()
            result["quote_characters"] = len(value["quote"])
        return result
    return value


ids = args.document_ids
if not ids:
    graph = request("POST", "/api/graph/build", json={})
    ids = [
        member["id"]
        for node in graph["nodes"]
        if set(node["arxiv_ids"]) & {"1706.03762", "2005.11401", "2309.15217"}
        for member in node["documents"]
    ]
    assert ids, "请导入 Transformer / RAG / Ragas 或明确指定 --document-id"
start = time.monotonic()
result = request("POST", "/api/writing/outline", json={"idea": args.idea, "document_ids": ids})
seconds = round(time.monotonic() - start, 2)
Path("/tmp/yanxi-writing-live.json").write_text(json.dumps(result, ensure_ascii=False, indent=2))
assert set(result["document_ids"]) == set(ids)
for heading in [
    "Abstract",
    "Introduction",
    "Related Work",
    "Proposed Method",
    "Evaluation Plan",
    "References",
]:
    assert heading in result["markdown"]
assert "研究计划草稿" in result["markdown"]
assert len(result["references"]) >= 1
for index, ref in enumerate(result["references"], 1):
    assert ref["number"] == index
    assert f"[{index}]" in result["markdown"]
    assert ref["document_id"] in ids
    source = ref["evidence"]
    raw = request("GET", f"/api/documents/{source['document_id']}/pages/{source['page']}")["text"]
    assert raw[source["start"] : source["end"]] == source["quote"]
    assert hashlib.sha256(raw.encode()).hexdigest() == source["page_sha256"]
    if ref["source_url"]:
        assert ref["source_url"].startswith("https://arxiv.org/abs/")
        assert ref["arxiv_id"] in ref["source_url"]
        assert ref["source_url"] in result["markdown"]
node_ids = {node["id"] for node in result["diagram"]["nodes"]}
assert result["diagram"]["mermaid"].startswith("flowchart TD\n")
assert len(result["diagram"]["edges"]) == len(node_ids) - 1
for edge in result["diagram"]["edges"]:
    assert edge["source"] in node_ids and edge["target"] in node_ids
    assert f"{edge['source']} --> {edge['target']}" in result["diagram"]["mermaid"]
check = {"document_ids": ids, "fingerprint": result["fingerprint"]}
assert request("POST", "/api/writing/check", json=check)["current"]
assert client.post("/api/writing/check", json={**check, "fingerprint": "0" * 64}).status_code == 409
record = {
    "verified_at": datetime.now(timezone.utc).isoformat(),
    "model": request("GET", "/api/health")["model"],
    "seconds": seconds,
    "selected_pdfs": len(ids),
    "referenced_works": len(result["references"]),
    "source_offsets_and_hashes_verified": True,
    "bibliography_scope_verified": True,
    "stale_check_verified": True,
    "diagram_structure_verified": True,
    "markdown_sha256": hashlib.sha256(result["markdown"].encode()).hexdigest(),
    "result": public_record(result),
    "limitations": "该验证检查结构、真实书目、逐字来源、数值校验路径与计划标记；不证明模型生成方案具有创新性、实验已完成或所有结论语义完全正确。Mermaid 图是研究计划流程，未自动执行任何脚本。",
}
if args.output:
    args.output.write_text(json.dumps(record, ensure_ascii=False, indent=2) + "\n")
print(
    f"Verified {len(ids)} PDFs / {len(result['references'])} works in {seconds}s; output: {args.output}"
)
