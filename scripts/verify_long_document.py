"""真实 541 页教材及现有论文回归；不下载资料、不自动覆盖版本决策。

默认复用相同 PDF。--reindex 会重新索引这份教材；--process-pid 仅在服务所在
Linux 主机采样内存，GPU 用量为整卡采样，不能当作精确的任务增量或峰值。
"""

import argparse
import hashlib
import json
import logging
import re
import subprocess
import time
from datetime import datetime, timezone
from importlib.metadata import version
from logging.handlers import BufferingHandler
from pathlib import Path

import httpx
from pypdf import PdfReader


def digest(text):
    return hashlib.sha256(text.encode()).hexdigest()


def resource_sample(pid):
    if not pid:
        return None
    status = Path(f"/proc/{pid}/status").read_text()
    rss = re.search(r"^VmRSS:\s+(\d+) kB", status, re.M)
    sample = {"rss_mib": round(int(rss[1]) / 1024, 2) if rss else None}
    try:
        result = subprocess.run(
            ["nvidia-smi", "--query-gpu=index,memory.used", "--format=csv,noheader,nounits"],
            capture_output=True,
            text=True,
            timeout=5,
            check=True,
        )
        sample["gpu_used_mib"] = {
            line.split(",")[0].strip(): int(line.split(",")[1])
            for line in result.stdout.splitlines()
        }
    except (OSError, ValueError, subprocess.SubprocessError):
        sample["gpu_used_mib"] = None
    return sample


def normalized(text):
    return re.sub(r"\s+", "", text).lower()


def run(args):
    pdf_bytes = args.pdf.read_bytes()
    doc_id = hashlib.sha256(pdf_bytes).hexdigest()
    reader = PdfReader(args.pdf)
    assert len(reader.pages) == 541, "此回归针对赛题指定的 541 页教材版本"
    samples, progress = [], []
    with httpx.Client(base_url=args.base_url, trust_env=False, timeout=180) as client:

        def request(method, path, **kwargs):
            response = client.request(method, path, **kwargs)
            response.raise_for_status()
            return response.json()

        started = time.monotonic()
        if args.process_pid:
            samples.append(resource_sample(args.process_pid))
        uploaded = request(
            "POST", "/api/documents", files={"file": (args.pdf.name, pdf_bytes, "application/pdf")}
        )
        assert uploaded["id"] == doc_id
        indexing_run = not uploaded["duplicate"]
        if args.reindex and uploaded["duplicate"]:
            request("POST", f"/api/documents/{doc_id}/reindex")
            indexing_run = True
        while time.monotonic() - started < args.timeout:
            docs = request("GET", "/api/documents")
            book = next(d for d in docs if d["id"] == doc_id)
            progress.append(
                {
                    "seconds": round(time.monotonic() - started, 2),
                    "status": book["status"],
                    "processed_pages": book["processed_pages"],
                }
            )
            if args.process_pid:
                samples.append(resource_sample(args.process_pid))
            if book["status"] not in ("queued", "processing"):
                break
            if len(progress) % 5 == 0:
                print(f"Indexing: {book['processed_pages']}/541", flush=True)
            time.sleep(2)
        assert book["status"] == "ready", f"教材未完成索引：{book['status']} {book['error']}"
        assert book["pages"] == book["processed_pages"] == 541
        index_seconds = round(time.monotonic() - started, 2) if indexing_run else None
        print(f"Ready: 541 pages; reindexed={indexing_run}; seconds={index_seconds}", flush=True)

        # 逐页比较完整文本，不能以最后一页计数代替全书验证。
        page_hashes, empty_pages, warning_pages = [], [], []
        collector = BufferingHandler(1000)
        collector.setLevel(logging.WARNING)
        logger = logging.getLogger("pypdf")
        logger.addHandler(collector)
        try:
            for number, original in enumerate(reader.pages, 1):
                collector.flush()
                page = request("GET", f"/api/documents/{doc_id}/pages/{number}")
                expected = original.extract_text() or ""
                assert page["page"] == number and page["method"] == "native"
                assert page["text"] == expected, f"第 {number} 页原文不一致"
                if collector.buffer:
                    assert page.get("warning"), f"第 {number} 页解析警告未保留"
                    assert any(f"第 {number} 页：" in item for item in book["warnings"])
                    warning_pages.append({"page": number, "warning": page["warning"]})
                page_hashes.append(digest(page["text"]))
                if not expected.strip():
                    empty_pages.append(number)
        finally:
            logger.removeHandler(collector)
            collector.close()
        assert request_status(client, f"/api/documents/{doc_id}/pages/542") == 404
        raw = client.get(f"/api/documents/{doc_id}/pdf").raise_for_status().content
        assert hashlib.sha256(raw).hexdigest() == doc_id
        image = client.get(f"/api/documents/{doc_id}/render/435").raise_for_status()
        assert image.content.startswith(b"\x89PNG\r\n\x1a\n")
        formula_page = request("GET", f"/api/documents/{doc_id}/pages/205")["text"]
        assert not any(ord(c) < 32 and c not in "\n\t\r" for c in formula_page)

        by_arxiv = {
            d["arxiv_id"]: d["id"] for d in docs if d["status"] == "ready" and d["arxiv_id"]
        }
        transformer, rag = by_arxiv["1706.03762"], by_arxiv["2005.11401"]
        scan = next(
            d["id"]
            for d in docs
            if d["status"] == "ready"
            and d["ocr_pages"] == d["pages"] == 15
            and "attention" in d["title"].lower()
        )
        cases = [
            (
                "book_chapter_authors",
                "这本书第21章 Deep learning and ethics 是由哪两位作者撰写的？",
                [doc_id],
                ["Travis LaCroix", "Simon J.D. Prince"],
                [435],
            ),
            (
                "book_figure_discount",
                "按照书中 Figure 19.18，discount factor 的数值是多少？",
                [doc_id],
                ["0.9"],
                [415],
            ),
            (
                "book_figure_ratio",
                "按照书中 Figure 20.3，ImageNet 上大多数模型参数数量是训练样本数量的多少倍？",
                [doc_id],
                ["10", "100"],
                [420],
            ),
            (
                "paper_numeric_detail",
                "Transformer 的前馈网络 inner-layer dimension 是多少？",
                [transformer],
                ["2048"],
                [5],
            ),
            (
                "scan_numeric_detail",
                "Position-wise Feed-Forward Networks 的 inner-layer dimension 是多少？",
                [scan],
                ["2048"],
                [5],
            ),
            (
                "cross_paper_mechanisms",
                "Transformer 如何处理序列，RAG 如何结合参数记忆与外部知识？请分别依据两篇论文概括主要机制。",
                [transformer, rag],
                ["注意力", "参数"],
                [],
            ),
            (
                "insufficient_evidence",
                "这篇论文中作者的银行卡号和实验室今日温度是多少？",
                [transformer],
                [],
                [],
            ),
        ]
        results = []
        for name, question, scope, expected, pages in cases:
            began = time.monotonic()
            result = request(
                "POST", "/api/chat", json={"question": question, "document_ids": scope}
            )
            refused = name == "insufficient_evidence"
            assert result["insufficient_evidence"] == refused, f"{name}: {result['answer']}"
            citations = result["citations"]
            assert bool(citations) != refused
            for text in expected:
                assert normalized(text) in normalized(result["answer"]), (
                    f"{name}: 缺少预期答案 {text}"
                )
            if pages:
                assert set(pages) <= {c["page"] for c in citations}, f"{name}: 来源页码错误"
            if name == "cross_paper_mechanisms":
                assert {c["document_id"] for c in citations} == set(scope)
            public_citations = []
            for citation in citations:
                assert citation["document_id"] in scope
                page = request(
                    "GET", f"/api/documents/{citation['document_id']}/pages/{citation['page']}"
                )
                assert normalized(citation["quote"]) in normalized(page["text"])
                public_citations.append(
                    {
                        **{k: v for k, v in citation.items() if k != "quote"},
                        "quote_sha256": digest(citation["quote"]),
                        "page_sha256": digest(page["text"]),
                    }
                )
            results.append(
                {
                    "case": name,
                    "question": question,
                    "document_ids": scope,
                    "seconds": round(time.monotonic() - began, 2),
                    "answer": result["answer"],
                    "insufficient_evidence": refused,
                    "citations": public_citations,
                    "passed": True,
                }
            )
            print(f"PASS {name}: {results[-1]['seconds']}s", flush=True)

        resources = None
        if samples:
            resources = {
                "samples": len(samples),
                "rss_before_mib": samples[0]["rss_mib"],
                "rss_sampled_max_mib": max(
                    s["rss_mib"] for s in samples if s["rss_mib"] is not None
                ),
                "gpu_sampled_max_mib": {
                    str(i): max(
                        s["gpu_used_mib"][str(i)]
                        for s in samples
                        if s["gpu_used_mib"] and str(i) in s["gpu_used_mib"]
                    )
                    for i in range(4)
                }
                if any(s["gpu_used_mib"] for s in samples)
                else None,
                "note": "索引期间每约两秒采样，可能漏掉瞬时峰值；RSS 含常驻模型，GPU 为整卡用量，不能代表本任务独占增量。",
            }
        return {
            "verified_at": datetime.now(timezone.utc).isoformat(),
            "fixture": {
                "filename": args.pdf.name,
                "sha256": doc_id,
                "bytes": len(pdf_bytes),
                "pages": 541,
                "source_url": args.source_url,
                "origin": "作者公开发布，同赛题文件名与页数；未经组织方资料包哈希比对，不声称已确认官方原件一致",
            },
            "runtime": {
                "pypdf": version("pypdf"),
                "fonttools": version("fonttools"),
                "health": request("GET", "/api/health"),
            },
            "indexing": {
                "performed": indexing_run,
                "seconds": index_seconds,
                "progress": progress,
                "resources": resources,
            },
            "verification": {
                "all_541_pages_match_same_parser_output": True,
                "parser_warning_pages": warning_pages,
                "page_hashes_sha256": digest("\n".join(page_hashes)),
                "empty_pages": empty_pages,
                "pdf_download_hash_matches": True,
                "out_of_range_page_rejected": True,
                "page435_png_rendered": True,
                "page205_cff_control_characters": 0,
            },
            "cases": results,
            "limits": "逐页比对采用同一版本 pypdf，验证解析与存储可复现，不证明全部内容被提取；PDF 库触发的内容跳过保护会保留警告。七个固定问答用例不是通用正确率或完整语义评测；扫描件为既有自行栅格化样本，非组织方 img 文件；未验证表格/公式结构化、全书翻译、大规模并发。",
        }


def request_status(client, path):
    return client.get(path).status_code


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-url", default="http://127.0.0.1:18474")
    parser.add_argument("--pdf", type=Path, required=True)
    parser.add_argument("--reindex", action="store_true")
    parser.add_argument("--timeout", type=int, default=1800)
    parser.add_argument("--process-pid", type=int)
    parser.add_argument(
        "--source-url",
        default="https://github.com/udlbook/udlbook/releases/download/v5.0.3/UnderstandingDeepLearning_02_09_26_C.pdf",
    )
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    result = run(args)
    args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n")
    print(f"Verified 541 pages / {len(result['cases'])} cases; output: {args.output}")
