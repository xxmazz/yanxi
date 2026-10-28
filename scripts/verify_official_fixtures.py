"""核对本地比赛资料包，验证官方扫描件及原生版本；不下载或公开 PDF。

先在临时 Store 重现 v7 → v1，正文去重不加载 E5；再检查真实服务。
--keep-scan 明确允许将扫描件的相似候选逐一选择保留两者，不替换已有文献。
"""

import argparse
import hashlib
import json
import re
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path

import httpx
from pypdf import PdfReader

from backend.core import Store, chunks_for_page
from backend.parsing import extract_page, metadata


def sha(data):
    return hashlib.sha256(data).hexdigest()


def normalized(text):
    return re.sub(r"\s+", "", text).lower()


def isolated_versions(folder):
    class NoOCR:
        def page(self, *_):
            raise AssertionError("原生版本不应进入 OCR")

    with tempfile.TemporaryDirectory(prefix="yanxi-official-versions-") as temporary:
        store = Store(Path(temporary))
        ids = []
        candidate = None
        for number in (7, 1):
            path = folder / f"1706.03762v{number}.pdf"
            # 故意使用没有 arXiv 标识的文件名，检验正文而非文件名。
            doc, duplicate = store.create(path.read_bytes(), f"renamed-{number}.pdf")
            assert not duplicate
            reader = PdfReader(path)
            store.begin(doc["id"], len(reader.pages))
            pages = [
                extract_page(page, path, index, NoOCR())
                for index, page in enumerate(reader.pages, 1)
            ]
            chunks = []
            for index, page in enumerate(pages, 1):
                store.save_page(doc["id"], index, page)
                chunks.extend(chunks_for_page(page["text"], index))
            info = metadata(reader, pages[0])
            analysis = store.analyze(doc["id"], "\n".join(p["text"] for p in pages), chunks, info)
            store.finish(doc["id"], len(pages), chunks, info["title"], analysis, info)
            ids.append(doc["id"])
        newer, older = (store.document(doc_id) for doc_id in ids)
        assert newer["status"] == "ready" and newer["version"] == 7
        assert older["status"] == "review" and older["version"] == 1
        candidate = next(c for c in older["similar_documents"] if c["id"] == newer["id"])
        assert candidate["recommendation"] == "keep_existing"
        store.resolve(older["id"], newer["id"], "keep_existing")
        assert store.document(older["id"])["is_active"] == 0
        assert store.document(newer["id"])["is_active"] == 1
        assert {r["document_id"] for r in store.rows(ids)} == {newer["id"]}
        for number, doc_id in zip((7, 1), ids, strict=True):
            assert sha((store.root / (doc_id + ".pdf")).read_bytes()) == doc_id
            assert store.page(doc_id, 1)["text"]
        return {
            "order": [7, 1],
            "renamed_files": True,
            "candidate": candidate,
            "older_excluded_from_retrieval": True,
            "both_originals_and_pages_retained": True,
            "scope": "独立临时 Store、真实 PDF 与生产解析/去重/决策代码；不加载 E5，不覆盖线上已有版本选择。",
        }


def run(args):
    folder = args.fixture_dir / "pdf"
    manifest = []
    for path in sorted(args.fixture_dir.rglob("*.pdf")):
        manifest.append(
            {
                "file": path.relative_to(args.fixture_dir).as_posix(),
                "bytes": path.stat().st_size,
                "pages": len(PdfReader(path).pages),
                "sha256": sha(path.read_bytes()),
            }
        )
    fixtures = {Path(item["file"]).name: item for item in manifest}
    book = fixtures["UnderstandingDeepLearning_02_09_26_C.pdf"]
    assert book["pages"] == 541
    previous = json.loads(args.previous_book_report.read_text())
    assert book["sha256"] == previous["fixture"]["sha256"]
    assert previous["verification"]["all_541_pages_match_same_parser_output"]
    versions = isolated_versions(folder)
    print("PASS official hashes and isolated v7 -> v1", flush=True)
    scan_path = folder / "1706.03762v1_img.pdf"
    scan_id = fixtures[scan_path.name]["sha256"]
    assert all(not (p.extract_text() or "").strip() for p in PdfReader(scan_path).pages)
    with httpx.Client(base_url=args.base_url, trust_env=False, timeout=180) as client:

        def request(method, path, **kwargs):
            return client.request(method, path, **kwargs).raise_for_status().json()

        def documents():
            return request("GET", "/api/documents?include_archived=true")

        before = {d["id"]: (d["is_active"], d["trashed_at"]) for d in documents()}
        existing = next((d for d in documents() if d["id"] == scan_id), None)
        if existing is None:
            request(
                "POST",
                "/api/documents",
                files={"file": (scan_path.name, scan_path.read_bytes(), "application/pdf")},
            )
        start = time.monotonic()
        while True:
            scan = next(d for d in documents() if d["id"] == scan_id)
            if scan["status"] not in ("queued", "processing"):
                break
            assert time.monotonic() - start < args.timeout, "官方扫描件解析超时"
            time.sleep(2)
        choices = []
        while scan["status"] == "review" and args.keep_scan:
            candidate = scan["similar_documents"][0]
            choices.append(candidate)
            scan = request(
                "POST",
                f"/api/documents/{scan_id}/resolve",
                json={"existing_id": candidate["id"], "action": "keep_both"},
            )
        assert scan["status"] == "ready", "扫描件需要相似版本确认；可显式使用 --keep-scan"
        assert scan["ocr_pages"] == scan["pages"] == 15
        raw = client.get(f"/api/documents/{scan_id}/pdf").raise_for_status().content
        assert sha(raw) == scan_id
        page_records = []
        for index in range(1, 16):
            page = request("GET", f"/api/documents/{scan_id}/pages/{index}")
            assert page["method"] == "ocr" and page["text"].strip() and page["blocks"]
            for block in page["blocks"]:
                assert len(block["bbox"]) == 4 and 0 <= block["confidence"] <= 1
                assert all(
                    0 <= x <= page["width"] and 0 <= y <= page["height"] for x, y in block["bbox"]
                )
            page_records.append(
                {
                    "page": index,
                    "text_sha256": sha(page["text"].encode()),
                    "blocks": len(page["blocks"]),
                }
            )
        image = client.get(f"/api/documents/{scan_id}/render/5").raise_for_status()
        assert image.content.startswith(b"\x89PNG\r\n\x1a\n")
        # 对正文原生版本只读核对，既有 v1 的归档决策不恢复、不重新上传。
        docs = {d["id"]: d for d in documents()}
        for name in ("1706.03762v1.pdf", "1706.03762v7.pdf", book["file"].split("/")[-1]):
            doc_id = fixtures[name]["sha256"]
            assert docs[doc_id]["status"] == "ready"
            raw = client.get(f"/api/documents/{doc_id}/pdf").raise_for_status().content
            assert sha(raw) == doc_id
        assert docs[book["sha256"]]["pages"] == 541
        newer = fixtures["1706.03762v7.pdf"]["sha256"]
        cases = [
            (
                "official_scan_ffn",
                scan_id,
                "Position-wise Feed-Forward Networks 的 inner-layer dimension 是多少？",
                ["2048"],
                5,
            ),
            (
                "official_author",
                newer,
                "论文 Attention Is All You Need 的第6位作者是谁，署名机构是什么？",
                ["Aidan", "Gomez", "Toronto"],
                1,
            ),
            (
                "official_adam",
                newer,
                "这篇论文中 Adam 优化器的 β1 和 β2 分别是多少？",
                ["0.9", "0.98"],
                7,
            ),
            (
                "official_hardware",
                newer,
                "Transformer 在训练 Base 模型时用了多少个 GPU，具体型号是什么？",
                ["8", "P100"],
                7,
            ),
            (
                "official_layernorm_reference",
                newer,
                "请列出论文引用的 Layer Normalization 参考文献的完整作者和年份。",
                ["Jimmy Lei Ba", "Jamie Ryan Kiros", "Geoffrey", "Hinton", "2016"],
                10,
            ),
        ]
        results = []
        for name, doc_id, question, expected, expected_page in cases:
            start = time.monotonic()
            answer = request(
                "POST", "/api/chat", json={"question": question, "document_ids": [doc_id]}
            )
            citations = []
            for citation in answer["citations"]:
                assert citation["document_id"] == doc_id
                page = request("GET", f"/api/documents/{doc_id}/pages/{citation['page']}")
                assert normalized(citation["quote"]) in normalized(page["text"])
                citations.append(
                    {k: v for k, v in citation.items() if k != "quote"}
                    | {"quote_sha256": sha(citation["quote"].encode())}
                )
            passed = (
                not answer["insufficient_evidence"]
                and all(normalized(text) in normalized(answer["answer"]) for text in expected)
                and expected_page in {c["page"] for c in citations}
            )
            results.append(
                {
                    "case": name,
                    "question": question,
                    "expected_keywords": expected,
                    "expected_page": expected_page,
                    "answer": answer["answer"],
                    "insufficient_evidence": answer["insufficient_evidence"],
                    "validation_error": answer.get("validation_error"),
                    "citations": citations,
                    "seconds": round(time.monotonic() - start, 2),
                    "passed": passed,
                }
            )
            print(f"{'PASS' if passed else 'FAIL'} {name}", flush=True)
        after = {d["id"]: (d["is_active"], d["trashed_at"]) for d in documents()}
        assert all(after[key] == value for key, value in before.items() if key != scan_id)
        return {
            "verified_at": datetime.now(timezone.utc).isoformat(),
            "provenance": "用户提供的比赛资料包；原件仅保留在本地下载目录与服务器 runtime，仓库仅保存哈希及验证结果。",
            "manifest": manifest,
            "book": {
                "sha256": book["sha256"],
                "matches_previous_541_page_run": True,
                "previous_report_sha256": sha(args.previous_book_report.read_bytes()),
                "note": "沿用同字节原件的上一轮逐页与七问验证；本轮不重新计时报作全书重索引。",
            },
            "isolated_versions": versions,
            "scan": {
                "id": scan_id,
                "native_text_absent_all_15_pages": True,
                "ocr_pages": 15,
                "original_download_hash_matches": True,
                "page5_png_rendered": True,
                "keep_both_candidates": choices,
                "page_records": page_records,
            },
            "health": request("GET", "/api/health"),
            "cases": results,
            "existing_document_selections_unchanged": True,
            "limits": "逐页 OCR 文本非空、坐标及引用核对不等于 OCR 全文准确率；独立版本去重未加载 E5；仅覆盖清单中指定问答，不代表全部赛题通过。",
        }


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--fixture-dir", type=Path, required=True)
    parser.add_argument("--base-url", default="http://127.0.0.1:18474")
    parser.add_argument(
        "--previous-book-report",
        type=Path,
        default=Path("docs/LONG_DOCUMENT_VERIFICATION_20261027.json"),
    )
    parser.add_argument("--keep-scan", action="store_true")
    parser.add_argument("--timeout", type=int, default=1200)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    result = run(args)
    args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n")
    if not all(case["passed"] for case in result["cases"]):
        raise SystemExit("部分问答未通过，实际结果已保存")
    print(f"Verified official fixtures; output: {args.output}")
