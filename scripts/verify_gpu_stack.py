"""隔离全模型服务验收：真实PDF、E5检索、Qwen问答与官方扫描件OCR。

必须显式使用 --isolated-library，且服务文献库最初为空；只接受回环入口，
拒绝正式服务常用18474端口。不启动服务、下载权重、调用云模型或删除文献。
--keep-scan 才允许对本轮上传的扫描件逐一保留两版；--with-bert 增加双篇比较。
"""

import argparse
import hashlib
import json
import re
import time
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlsplit

import httpx
from pypdf import PdfReader

FFN_QUESTION = "Position-wise Feed-Forward Networks 的 inner-layer dimension 是多少？"
COMPARISON_QUESTION = (
    "根据 Attention Is All You Need 和 BERT，BERT 的架构和原始 Transformer 的 Encoder "
    "有什么承袭关系？BERT 丢弃了原始 Transformer 的哪个部分？"
)


def sha(content):
    return hashlib.sha256(content).hexdigest()


def normalized(text):
    return re.sub(r"\s+", "", text).lower()


def raw_interval(text, quote):
    start = text.find(quote)
    if start >= 0:
        return start, start + len(quote), "exact"
    letters, offsets = [], []
    for index, char in enumerate(text):
        if not char.isspace():
            for lowered in char.lower():
                letters.append(lowered)
                offsets.append(index)
    target = normalized(quote)
    start = "".join(letters).find(target) if target else -1
    if start < 0:
        raise ValueError("引用摘录不在保存原页的连续文本中")
    return offsets[start], offsets[start + len(target) - 1] + 1, "whitespace_case_normalized"


class Verification:
    def __init__(self, args):
        self.args = args
        self.client = httpx.Client(
            base_url=args.base_url,
            trust_env=False,
            timeout=httpx.Timeout(300, connect=10),
            headers={"X-Yanxi-Request": "1"},
        )
        self.owned = set()
        self.report = {
            "verified_at": datetime.now(timezone.utc).isoformat(),
            "base_url": args.base_url,
            "isolated_library_asserted": args.isolated_library,
            "script_sha256": sha(Path(__file__).read_bytes()),
            "fixtures": [],
            "checks": [],
            "question_cases": [],
            "scan_version_choices": [],
            "service_started_or_stopped_by_script": False,
            "model_download_or_cloud_api_requested": False,
            "vector_row_counts_verified_via_api": False,
            "limits": [
                "本脚本仅验证显式指定的隔离服务；不会启动或停止服务，也不删除文献。运行者须负责服务和GPU清理。",
                "模型ready和真实问答不能单独证明GPU设备分配；运行者还须核对模型设备映射、nvidia-smi及显存占用。",
                "E5已加载、混合检索步骤不等于每段向量都已入库；API不暴露向量行数，须由运行者只读核对隔离数据库。",
                "引用逐字位置、预期数值及来源覆盖检查不等于完整语义蕴含或OCR准确率；中文结论仍需人工核对。",
                "只覆盖指定原生PDF、官方扫描件和可选BERT，不代表所有官方样本、全新操作系统或公网浏览器验收。",
            ],
        }

    def request(self, method, path, **kwargs):
        return self.client.request(method, path, **kwargs).raise_for_status().json()

    def check(self, name, condition, **details):
        self.report["checks"].append({"name": name, "passed": bool(condition), **details})
        if not condition:
            raise AssertionError(name)

    def documents(self):
        return self.request("GET", "/api/documents?include_archived=true")

    def guard_owned_library(self):
        documents = self.documents()
        if not {doc["id"] for doc in documents} <= self.owned:
            raise RuntimeError("发现本轮未上传的文献，停止后续写入；请确认隔离服务入口")
        return documents

    def fixture(self, filename, scan=False):
        path = self.args.fixture_dir / filename
        content = path.read_bytes()
        reader = PdfReader(path)
        record = {
            "filename": filename,
            "sha256": sha(content),
            "bytes": len(content),
            "pages": len(reader.pages),
        }
        if scan:
            record["native_text_absent_on_all_pages"] = all(
                not (page.extract_text() or "").strip() for page in reader.pages
            )
            self.check(
                "official_scan_has_no_native_text",
                record["native_text_absent_on_all_pages"] and record["pages"] == 15,
            )
        self.report["fixtures"].append(record)
        return content, record

    def upload(self, filename, scan=False):
        self.guard_owned_library()
        content, fixture = self.fixture(filename, scan=scan)
        started = time.monotonic()
        result = self.request(
            "POST", "/api/documents", files={"file": (filename, content, "application/pdf")}
        )
        self.check("uploaded_pdf_identity", result["id"] == fixture["sha256"], filename=filename)
        self.owned.add(result["id"])
        document = self.wait_document(result["id"], filename)
        if scan and document["status"] == "review" and self.args.keep_scan:
            for _ in range(10):
                if document["status"] != "review":
                    break
                self.guard_owned_library()
                candidate = document["similar_documents"][0]
                if candidate["id"] not in self.owned or candidate["id"] == document["id"]:
                    raise RuntimeError("扫描件版本候选不属于本轮上传范围，停止自动保留")
                document = self.request(
                    "POST",
                    f"/api/documents/{document['id']}/resolve",
                    json={"existing_id": candidate["id"], "action": "keep_both"},
                )
                self.report["scan_version_choices"].append(
                    {
                        "document_id": document["id"],
                        "existing_id": candidate["id"],
                        "action": "keep_both",
                    }
                )
        fixture["final_status"] = document["status"]
        fixture["processed_pages"] = document["processed_pages"]
        fixture["ocr_pages"] = document["ocr_pages"]
        fixture["warnings"] = document.get("warnings", [])
        fixture["upload_to_ready_seconds"] = round(time.monotonic() - started, 3)
        self.check(
            "uploaded_document_ready_and_active",
            document["status"] == "ready"
            and document["is_active"]
            and not document["trashed_at"]
            and document["pages"] == document["processed_pages"] == fixture["pages"],
            filename=filename,
            status=document["status"],
            note="扫描件若处于review，需显式--keep-scan才可保留两版" if scan else "",
        )
        if scan:
            self.check("all_scan_pages_used_ocr", document["ocr_pages"] == fixture["pages"])
        else:
            self.check(
                "native_pdf_did_not_require_ocr", document["ocr_pages"] == 0, filename=filename
            )
        downloaded = (
            self.client.get(f"/api/documents/{document['id']}/pdf").raise_for_status().content
        )
        self.check(
            "downloaded_pdf_sha256_matches", sha(downloaded) == fixture["sha256"], filename=filename
        )
        page_records = []
        for number in range(1, fixture["pages"] + 1):
            page = self.request("GET", f"/api/documents/{document['id']}/pages/{number}")
            if not page["text"].strip() or (
                scan and (page["method"] != "ocr" or not page["blocks"])
            ):
                raise AssertionError(f"{filename}第{number}页未保存有效解析正文")
            if scan:
                for block in page["blocks"]:
                    if not (
                        len(block["bbox"]) == 4
                        and 0 <= block["confidence"] <= 1
                        and all(
                            0 <= x <= page["width"] and 0 <= y <= page["height"]
                            for x, y in block["bbox"]
                        )
                    ):
                        raise AssertionError("OCR坐标或置信度超出页面范围")
            page_records.append(
                {
                    "page": number,
                    "method": page["method"],
                    "text_sha256": sha(page["text"].encode()),
                    "blocks": len(page["blocks"]),
                }
            )
        fixture["page_records"] = page_records
        print(f"PASS {filename}: {fixture['pages']}页，OCR {document['ocr_pages']}页", flush=True)
        return document["id"]

    def wait_document(self, doc_id, filename):
        started, logged = time.monotonic(), 0.0
        while time.monotonic() - started < self.args.parse_timeout:
            docs = self.guard_owned_library()
            current = next((doc for doc in docs if doc["id"] == doc_id), None)
            if current is None:
                raise RuntimeError("已上传文献的状态记录不存在")
            elapsed = time.monotonic() - started
            if current["status"] not in {"queued", "processing"}:
                return current
            if elapsed - logged >= 10:
                print(
                    f"等待 {filename}: {current['processed_pages']}/{current['pages']}页，{elapsed:.0f}秒",
                    flush=True,
                )
                logged = elapsed
            time.sleep(2)
        raise TimeoutError(f"{filename}超过本次{self.args.parse_timeout}秒解析等待预算")

    def verify_citation(self, citation, scope):
        if citation["document_id"] not in scope or not citation["quote"].strip():
            raise ValueError("引用来源不属于所选范围或摘录为空")
        page = self.request(
            "GET", f"/api/documents/{citation['document_id']}/pages/{citation['page']}"
        )
        start, end, kind = raw_interval(page["text"], citation["quote"])
        return {key: citation[key] for key in ["id", "document_id", "title", "page"]} | {
            "method": page["method"],
            "start": start,
            "end": end,
            "match": kind,
            "page_sha256": sha(page["text"].encode()),
            "quote_sha256": sha(citation["quote"].encode()),
            "raw_interval_sha256": sha(page["text"][start:end].encode()),
        }

    def question(self, name, scope, question, terms, expected_page, model_answer=True, scan=False):
        self.guard_owned_library()
        started = time.monotonic()
        case = {
            "name": name,
            "question": question,
            "document_ids": scope,
            "expected_terms": terms,
            "expected_physical_page": expected_page,
            "requires_qwen_answer_path": model_answer,
        }
        self.report["question_cases"].append(case)
        print(f"开始真实问答：{name}（单次HTTP等待最多300秒）", flush=True)
        try:
            response = self.request(
                "POST", "/api/chat", json={"question": question, "document_ids": scope}
            )
            # 引用或范围检查失败时仍保留实际回答，便于复查，不把失败改成通过。
            case.update(
                answer=response["answer"],
                insufficient_evidence=response["insufficient_evidence"],
                validation_error=response.get("validation_error"),
                steps=response.get("steps", []),
            )
            citations = [self.verify_citation(item, scope) for item in response["citations"]]
            labels = set(re.findall(r"\[(E\d+)\]", response["answer"]))
            checks = {
                "not_refused": response["insufficient_evidence"] is False,
                "expected_terms": all(
                    normalized(term) in normalized(response["answer"]) for term in terms
                ),
                "all_scope_documents_cited": {item["document_id"] for item in citations}
                == set(scope),
                "expected_physical_page": expected_page in {item["page"] for item in citations},
                "citation_labels_known": bool(labels)
                and labels <= {item["id"] for item in citations},
                "qwen_answer_path": not model_answer
                or "本地模型阅读证据" in response.get("steps", []),
                "e5_mixed_retrieval_path": not model_answer
                or "混合检索" in response.get("steps", []),
                "ocr_source": not scan or all(item["method"] == "ocr" for item in citations),
            }
            case.update(
                citations=citations,
                checks=checks,
                automated_checks_passed=all(checks.values()),
            )
        except Exception as exc:
            case.update(
                automated_checks_passed=False,
                error={"type": type(exc).__name__, "message": str(exc)[:400]},
            )
        case["seconds"] = round(time.monotonic() - started, 3)
        case["semantic_review_required"] = True
        print(f"{'PASS' if case['automated_checks_passed'] else 'FAIL'} {name}", flush=True)

    def run(self):
        self.check("isolated_library_initially_empty", self.documents() == [])
        self.report["initial_document_count"] = 0
        health = self.request("GET", "/api/health")
        self.report["health"] = health
        self.check(
            "all_three_local_model_components_ready",
            all(health.get(key) is True for key in ["model_ready", "embedding_ready", "ocr_ready"])
            and health.get("retrieval") == "bm25+e5-rrf",
        )
        native = self.upload("1706.03762v7.pdf")
        self.question("native_ffn_dimension", [native], FFN_QUESTION, ["2048"], 5)
        scan = self.upload("1706.03762v1_img.pdf", scan=True)
        self.question("official_scan_ffn_dimension", [scan], FFN_QUESTION, ["2048"], 5, scan=True)
        if self.args.with_bert:
            bert = self.upload("N19-1423.pdf")
            self.question(
                "transformer_bert_architecture",
                [native, bert],
                COMPARISON_QUESTION,
                ["BERT", "Transformer", "未采用", "解码器", "归纳"],
                3,
                model_answer=False,
            )
            self.report["question_cases"][-1]["execution_note"] = (
                "架构问题可能走服务端确定性事实处理器，不能把这条单独当成Qwen生成验证；原生与OCR的FFN用例要求Qwen回答路径。"
            )
        documents = self.guard_owned_library()
        self.check(
            "all_uploaded_documents_remain_ready_and_active",
            len(documents) == len(self.owned)
            and all(
                doc["status"] == "ready" and doc["is_active"] and not doc["trashed_at"]
                for doc in documents
            ),
        )
        self.report["final_documents"] = [
            {
                key: doc[key]
                for key in [
                    "id",
                    "title",
                    "pages",
                    "status",
                    "ocr_pages",
                    "is_active",
                    "trashed_at",
                ]
            }
            for doc in documents
        ]
        self.report["all_automated_checks_passed"] = all(
            case["automated_checks_passed"] for case in self.report["question_cases"]
        )


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-url", default="http://127.0.0.1:18476")
    parser.add_argument(
        "--fixture-dir",
        type=Path,
        required=True,
        help="直接包含官方PDF的目录；也可传其含pdf子目录的上层目录",
    )
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument(
        "--isolated-library",
        action="store_true",
        required=True,
        help="明确确认该入口是独立服务且初始库为空",
    )
    parser.add_argument(
        "--keep-scan",
        action="store_true",
        help="仅对本轮扫描件候选逐一保留两者，不替换、归档或删除其他文献",
    )
    parser.add_argument("--with-bert", action="store_true", help="增加BERT上传与双篇架构比较")
    parser.add_argument("--parse-timeout", type=int, default=900)
    args = parser.parse_args()
    try:
        target = urlsplit(args.base_url)
        target_port = target.port
    except ValueError:
        parser.error("隔离服务URL格式错误")
    if (
        target.scheme not in {"http", "https"}
        or target.hostname not in {"localhost", "127.0.0.1", "::1"}
        or target_port == 18474
        or target.username is not None
        or target.password is not None
        or target.path not in {"", "/"}
        or target.query
        or target.fragment
    ):
        parser.error("仅接受回环隔离入口，且拒绝正式服务常用18474端口及URL凭据")
    args.fixture_dir = args.fixture_dir.expanduser().resolve()
    if (
        not (args.fixture_dir / "1706.03762v7.pdf").is_file()
        and (args.fixture_dir / "pdf").is_dir()
    ):
        args.fixture_dir /= "pdf"
    names = ["1706.03762v7.pdf", "1706.03762v1_img.pdf"] + (
        ["N19-1423.pdf"] if args.with_bert else []
    )
    if (
        not all((args.fixture_dir / name).is_file() for name in names)
        or not 1 <= args.parse_timeout <= 1800
    ):
        parser.error("缺少所需官方PDF，或解析等待预算不在1—1800秒范围")
    args.output.parent.mkdir(parents=True, exist_ok=True)
    verification = Verification(args)
    try:
        verification.run()
    except Exception as exc:
        verification.report["all_automated_checks_passed"] = False
        verification.report["error"] = {"type": type(exc).__name__, "message": str(exc)[:400]}
    finally:
        verification.client.close()
        verification.report["finished_at"] = datetime.now(timezone.utc).isoformat()
        args.output.write_text(json.dumps(verification.report, ensure_ascii=False, indent=2) + "\n")
    print(
        json.dumps(
            {
                "report": str(args.output),
                "all_automated_checks_passed": verification.report["all_automated_checks_passed"],
                "error": verification.report.get("error"),
            },
            ensure_ascii=False,
        )
    )
    return 0 if verification.report["all_automated_checks_passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
