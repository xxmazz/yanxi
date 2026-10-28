"""只读核对 GPT 三篇原件、官方比较题与改写问题；不下载或修改文献。"""

import argparse
import hashlib
import json
import re
import time
from datetime import datetime, timezone
from pathlib import Path

import httpx

from scripts.verify_comparison_qa import public

GPT2_URL = "https://cdn.openai.com/better-language-models/language_models_are_unsupervised_multitask_learners.pdf"


def sha(data):
    return hashlib.sha256(data).hexdigest()


def normalized(text):
    return re.sub(r"\s+", "", text).lower()


def run(args):
    with httpx.Client(base_url=args.base_url, trust_env=False, timeout=180) as client:

        def get(url):
            return client.get(url).raise_for_status().json()

        before = {d["id"]: d for d in get("/api/documents?include_archived=true")}
        fixtures = []
        for generation, path in enumerate(
            [
                args.fixture_dir / "pdf/language_understanding_paper.pdf",
                args.gpt2_pdf,
                args.fixture_dir / "pdf/2005.14165v4.pdf",
            ],
            1,
        ):
            doc_id = sha(path.read_bytes())
            assert (
                doc_id in before
                and before[doc_id]["status"] == "ready"
                and before[doc_id]["is_active"]
            )
            data = client.get(f"/api/documents/{doc_id}/pdf").raise_for_status().content
            assert sha(data) == doc_id
            fixtures.append(
                {
                    "generation": generation,
                    "filename": path.name,
                    "sha256": doc_id,
                    "pages": before[doc_id]["pages"],
                    "bytes": len(data),
                    "source": GPT2_URL if generation == 2 else "用户提供的官方比赛资料包",
                }
            )
        ids = [f["sha256"] for f in fixtures]
        cases = [
            (
                "official_parameters",
                "对比GPT-1、GPT-2和GPT-3的论文，说明这些模型的参数的变化。",
                ids,
                "parameters",
            ),
            (
                "official_normalization",
                "GPT-2 相比于 GPT-1，在 Transformer 的 Layer Normalization（LN）位置上做了什么关键的调整？",
                ids[:2],
                "normalization",
            ),
            (
                "official_context",
                "从 GPT-1 到 GPT-3，模型的最大上下文 Token 长度（Context Window）分别递增到了多少？",
                ids,
                "context",
            ),
            (
                "reordered_context",
                "Compare the context window lengths of the three papers, separately in tokens.",
                list(reversed(ids)),
                "context",
            ),
            (
                "parameter_paraphrase",
                "分别比较三篇论文的参数规模，保留各自原表的 M/B 单位，并区分明确数值和归纳。",
                ids,
                "parameters",
            ),
            (
                "normalization_paraphrase",
                "比较两篇的 layer norm 位置：在哪个子块位置做了调整，末尾是否增加 LN？",
                ids[:2],
                "normalization",
            ),
            ("missing_paper", "比较三篇的参数数量", [ids[0], ids[1], "0" * 64], "missing"),
        ]
        results = []
        for name, question, scope, topic in cases:
            started = time.monotonic()
            response = (
                client.post("/api/chat", json={"question": question, "document_ids": scope})
                .raise_for_status()
                .json()
            )
            citations = response["citations"]
            answer = response["answer"]
            checks = {
                "refusal_matches": response["insufficient_evidence"] == (topic == "missing"),
                "scope": all(c["document_id"] in scope for c in citations),
                "coverage": not citations
                if topic == "missing"
                else {c["document_id"] for c in citations} == set(scope),
            }
            by_doc = {
                doc_id: "".join(
                    part
                    for part in answer.split("\n\n")
                    if any(f"[{c['id']}]" in part for c in citations if c["document_id"] == doc_id)
                )
                for doc_id in scope
            }
            if topic == "parameters":
                checks["gpt1_total_not_invented"] = "没有直接报告总参数量" in by_doc[ids[0]]
                checks["gpt1_inference_from_gpt2"] = all(
                    s in by_doc[ids[1]] for s in ("117M", "1542M", "归纳", "原始 GPT")
                )
                checks["gpt3_original_units"] = "175.0B" in by_doc[
                    ids[2]
                ] and "175亿" not in normalized(answer)
            elif topic == "context":
                checks["per_paper_lengths"] = all(
                    value in by_doc[doc_id] for doc_id, value in zip(ids, ("512", "1024", "2048"))
                )
            elif topic == "normalization":
                checks["gpt1_position_not_invented"] = "未说明" in by_doc[ids[0]]
                checks["gpt2_input_and_final_block"] = all(
                    term in by_doc[ids[1]] for term in ("输入", "归一化")
                ) and bool(re.search(r"最终|最后", by_doc[ids[1]]))
            for citation in citations:
                page = get(f"/api/documents/{citation['document_id']}/pages/{citation['page']}")
                checks[f"verbatim_{citation['id']}"] = normalized(citation["quote"]) in normalized(
                    page["text"]
                )
                citation["page_sha256"] = sha(page["text"].encode())
                if topic != "missing":
                    expected_page = {ids[0]: 5, ids[1]: 4, ids[2]: 8}
                    checks[f"physical_page_{citation['id']}"] = (
                        citation["page"] == expected_page[citation["document_id"]]
                    )
            results.append(
                {
                    "id": name,
                    "question": question,
                    "document_ids": scope,
                    "checks": checks,
                    "passed": all(checks.values()),
                    "response": public(response),
                    "seconds": round(time.monotonic() - started, 2),
                }
            )
            print(f"{'PASS' if results[-1]['passed'] else 'FAIL'} {name}: {answer}", flush=True)
        after = {d["id"]: d for d in get("/api/documents?include_archived=true")}
        selections_unchanged = set(before) == set(after) and all(
            (d["is_active"], d["trashed_at"]) == (after[key]["is_active"], after[key]["trashed_at"])
            for key, d in before.items()
        )
        return {
            "verified_at": datetime.now(timezone.utc).isoformat(),
            "fixtures": fixtures,
            "health": get("/api/health"),
            "cases": results,
            "document_selections_unchanged": selections_unchanged,
            "limits": "固定题、改写题及逐字引文核对不等于通用语义准确率。GPT-1 参数规模是依据 GPT-2 表格与等价描述归纳；仅支持明确的简单配置表和谓词。",
        }


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-url", default="http://127.0.0.1:18474")
    parser.add_argument("--fixture-dir", type=Path, required=True)
    parser.add_argument("--gpt2-pdf", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    result = run(args)
    args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n")
    if not result["document_selections_unchanged"] or not all(c["passed"] for c in result["cases"]):
        raise SystemExit("存在未通过检查，结果已保存")
