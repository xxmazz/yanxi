"""验证官方 BLEU 表格、两篇架构来源和通用跨篇事实；不上传或重索引 PDF。"""

import argparse
import hashlib
import json
import re
import time
from datetime import datetime, timezone
from pathlib import Path

import httpx


def sha(data):
    return hashlib.sha256(data).hexdigest()


def normalize(text):
    return re.sub(r"\s+", "", text).lower()


def public(value):
    if isinstance(value, list):
        return [public(v) for v in value]
    if isinstance(value, dict):
        result = {k: public(v) for k, v in value.items() if k != "quote"}
        if "quote" in value:
            result["quote_sha256"] = sha(value["quote"].encode())
        return result
    return value


def run(args):
    with httpx.Client(base_url=args.base_url, trust_env=False, timeout=180) as client:

        def request(method, url, **kwargs):
            return client.request(method, url, **kwargs).raise_for_status().json()

        docs = {d["id"]: d for d in request("GET", "/api/documents?include_archived=true")}
        fixtures = []
        for filename in ("1706.03762v7.pdf", "N19-1423.pdf"):
            path = args.fixture_dir / "pdf" / filename
            doc_id = sha(path.read_bytes())
            assert (
                doc_id in docs and docs[doc_id]["status"] == "ready" and docs[doc_id]["is_active"]
            )
            original = client.get(f"/api/documents/{doc_id}/pdf").raise_for_status().content
            assert sha(original) == doc_id
            fixtures.append(
                {
                    "filename": filename,
                    "sha256": doc_id,
                    "pages": docs[doc_id]["pages"],
                    "bytes": len(original),
                }
            )
        transformer, bert = [f["sha256"] for f in fixtures]
        cases = [
            {
                "id": "official_bleu_comparison",
                "question": "Transformer (big) 模型在 English-to-German 任务上的 BLEU 分数是多少？当时的 State-of-the-art 模型是多少？提升了多少？",
                "scope": [transformer],
                "terms": ["28.4", "ConvS2S Ensemble", "26.36", "2.04"],
                "page": 8,
                "calculation": "2.04",
            },
            {
                "id": "bleu_french_table_column",
                "question": "按表格，Transformer (big) 在 English-to-French 的 BLEU 分数是多少？",
                "scope": [transformer],
                "terms": ["41.8", "EN-FR"],
                "page": 8,
            },
            {
                "id": "bleu_other_model",
                "question": "Transformer (base model) 在 EN-DE 的 BLEU 分数是多少？",
                "scope": [transformer],
                "terms": ["27.3"],
                "page": 8,
            },
            {
                "id": "bleu_missing_cell",
                "question": "Deep-Att + PosUnk Ensemble 在 EN-DE 的 BLEU 分数是多少？",
                "scope": [transformer],
                "terms": ["单元格为空"],
                "page": 8,
                "refused": True,
            },
            {
                "id": "official_architecture_comparison",
                "question": "根据 Attention Is All You Need 和 BERT，BERT 的架构和原始 Transformer 的 Encoder 有什么承袭关系？BERT 丢弃了原始 Transformer 的哪个部分？",
                "scope": [transformer, bert],
                "terms": ["BERT", "Transformer", "未采用", "解码器", "归纳"],
                "page": 3,
                "both": True,
            },
            {
                "id": "unavailable_comparison_source",
                "question": "比较两篇论文的架构",
                "scope": [transformer, "0" * 64],
                "terms": ["已就绪"],
                "refused": True,
            },
        ]
        if args.rag_id:
            cases.append(
                {
                    "id": "local_model_cross_paper_facts",
                    "question": "Transformer 如何处理序列，RAG 如何结合参数记忆与外部知识？请分别依据两篇论文概括主要机制。",
                    "scope": [transformer, args.rag_id],
                    "terms": ["注意力", "参数"],
                    "both": True,
                }
            )
        results = []
        for case in cases:
            started = time.monotonic()
            response = request(
                "POST",
                "/api/chat",
                json={"question": case["question"], "document_ids": case["scope"]},
            )
            checks = {
                "refusal_matches": response["insufficient_evidence"] == case.get("refused", False),
                "expected_content": all(
                    term.lower() in response["answer"].lower() for term in case["terms"]
                ),
            }
            citations = response["citations"]
            if case.get("page"):
                checks["physical_page"] = case["page"] in {c["page"] for c in citations}
            if case.get("both"):
                checks["both_papers_actually_cited"] = {c["document_id"] for c in citations} == set(
                    case["scope"]
                )
            if case.get("calculation"):
                checks["calculation"] = (
                    response.get("calculation", {}).get("result") == case["calculation"]
                )
            if case["id"] == "unavailable_comparison_source":
                checks["no_citations_when_scope_invalid"] = not citations
            for citation in citations:
                assert citation["document_id"] in case["scope"]
                page = request(
                    "GET", f"/api/documents/{citation['document_id']}/pages/{citation['page']}"
                )
                assert normalize(citation["quote"]) in normalize(page["text"])
                citation["page_sha256"] = sha(page["text"].encode())
            results.append(
                {
                    "case": case,
                    "checks": checks,
                    "response": public(response),
                    "passed": all(checks.values()),
                    "seconds": round(time.monotonic() - started, 2),
                }
            )
            print(
                f"{'PASS' if all(checks.values()) else 'FAIL'} {case['id']}: {response['answer']}",
                flush=True,
            )
        return {
            "verified_at": datetime.now(timezone.utc).isoformat(),
            "fixtures": fixtures,
            "health": request("GET", "/api/health"),
            "cases": results,
            "limits": "固定问答与逐字来源核对不是通用语义正确率；BLEU 仅支持已确认的原生双列布局。架构归纳依赖明确谓词，通用比较逐篇核对事实，不保证回答总问题的全部子项。",
        }


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-url", default="http://127.0.0.1:18474")
    parser.add_argument("--fixture-dir", type=Path, required=True)
    parser.add_argument("--rag-id")
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    result = run(args)
    args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n")
    if not all(case["passed"] for case in result["cases"]):
        raise SystemExit("存在未通过用例，实际结果已保存")
