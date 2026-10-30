"""在隔离文献库副本上顺序回归主要API链路，不操作鼠标或浏览器。

服务、数据库副本和GPU清理由运行者负责。原件不上传、不重索引、不改版本选择；
翻译生成缓存/任务，写作和摘要调用真实本地模型。报告保留失败，不以重试覆盖。
"""

import argparse
import hashlib
import json
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlsplit

import httpx


def sha(value):
    return hashlib.sha256(value).hexdigest()


def run(args):
    url = urlsplit(args.base_url)
    if (
        not args.isolated_library
        or url.scheme not in {"http", "https"}
        or url.hostname not in {"localhost", "127.0.0.1", "::1"}
        or url.port in {None, 18474}
        or url.username
        or url.password
        or url.path not in {"", "/"}
        or url.query
        or url.fragment
    ):
        raise ValueError("必须显式确认隔离库，使用非18474的回环入口，不得携带凭据或路径")
    if args.output_dir.exists():
        raise ValueError("输出目录已存在，请使用新目录保留每轮真实结果")
    args.output_dir.mkdir(parents=True)
    client = httpx.Client(base_url=args.base_url, trust_env=False, timeout=180)

    def get(path):
        return client.get(path).raise_for_status().json()

    before = get("/api/documents?include_archived=true")
    health = get("/api/health")
    if not all(health.get(key) for key in ["model_ready", "embedding_ready", "ocr_ready"]):
        raise ValueError("Qwen、E5、OCR须全部就绪")
    docs = {doc["id"]: doc for doc in before}
    ids = {}
    for key, filename in {
        "native": "1706.03762v7.pdf",
        "scan": "1706.03762v1_img.pdf",
    }.items():
        doc_id = sha((args.fixture_dir / "pdf" / filename).read_bytes())
        doc = docs[doc_id]
        if doc["status"] != "ready" or not doc["is_active"] or doc["trashed_at"]:
            raise ValueError(f"隔离副本须有已确认的有效版本：{filename}")
        ids[key] = doc_id
    for key, arxiv in {"rag": "2005.11401", "ragas": "2309.15217"}.items():
        choices = [
            d
            for d in before
            if d["arxiv_id"] == arxiv
            and d["status"] == "ready"
            and d["is_active"]
            and not d["trashed_at"]
        ]
        if len(choices) != 1:
            raise ValueError(f"须有唯一有效的{key}文献")
        ids[key] = choices[0]["id"]

    steps = []

    def step(name, module, arguments, stdout_json=False, timeout=1200):
        target = args.output_dir / f"{name}.json"
        cmd = [sys.executable, "-m", module, "--base-url", args.base_url, *map(str, arguments)]
        if not stdout_json:
            cmd.extend(["--output", str(target)])
        start = time.monotonic()
        record = {"name": name, "module": module, "passed": False}
        try:
            result = subprocess.run(cmd, text=True, capture_output=True, timeout=timeout)
            log = result.stdout + "\n" + result.stderr
            record["returncode"] = result.returncode
            if result.returncode == 0 and stdout_json:
                target.write_text(
                    json.dumps(json.loads(result.stdout), ensure_ascii=False, indent=2) + "\n"
                )
            record["passed"] = result.returncode == 0 and target.is_file()
        except (subprocess.TimeoutExpired, ValueError) as exc:
            log = str(exc)
            record["error"] = type(exc).__name__
        (args.output_dir / f"{name}.log").write_text(log)
        record["seconds"] = round(time.monotonic() - start, 2)
        if target.is_file():
            record["report_sha256"] = sha(target.read_bytes())
        steps.append(record)
        print(f"{'PASS' if record['passed'] else 'FAIL'} {name}: {record['seconds']}s", flush=True)

    step("reading", "scripts.verify_reading_links", ["--fixture-dir", args.fixture_dir / "pdf"])
    step(
        "official_fixtures", "scripts.verify_official_fixtures", ["--fixture-dir", args.fixture_dir]
    )
    step(
        "graph20", "scripts.verify_graph20", ["--fixture-dir", args.fixture_dir / "pdf", "--review"]
    )
    step(
        "comparison",
        "scripts.verify_comparison_qa",
        ["--fixture-dir", args.fixture_dir, "--rag-id", ids["rag"]],
    )
    step(
        "gpt",
        "scripts.verify_gpt_comparison",
        ["--fixture-dir", args.fixture_dir, "--gpt2-pdf", args.gpt2_pdf],
    )
    step(
        "writing",
        "scripts.verify_writing",
        [
            item
            for doc_id in [ids["native"], ids["rag"], ids["ragas"]]
            for item in ["--document-id", doc_id]
        ],
    )
    step(
        "translation",
        "scripts.verify_translation",
        ["--document-id", ids["ragas"], "--whole"],
        stdout_json=True,
    )
    history = get("/api/research/tasks")
    refreshed = []
    if args.refresh_stale_history:
        for task in history:
            if task["status"] != "needs_summary":
                continue
            previous_ids = [item["document_id"] for item in task["selected"]]
            current = (
                client.post(f"/api/research/tasks/{task['id']}/retry").raise_for_status().json()
            )
            deadline = time.monotonic() + 300
            while current["status"] in {"queued", "running"}:
                if time.monotonic() > deadline:
                    raise TimeoutError("隔离历史任务补生成超时，保留任务供检查")
                time.sleep(1)
                current = get(f"/api/research/tasks/{task['id']}")
            refreshed.append(
                {
                    "task_id": task["id"],
                    "before_status": task["status"],
                    "after_status": current["status"],
                    "attempt": current["attempt"],
                    "document_ids_unchanged": previous_ids
                    == [item["document_id"] for item in current["selected"]],
                }
            )
        history = get("/api/research/tasks")
        steps.append(
            {
                "name": "refresh_stale_history",
                "passed": all(
                    t["after_status"] == "complete" and t["document_ids_unchanged"]
                    for t in refreshed
                ),
                "tasks": refreshed,
            }
        )
    tasks = [task for task in history if task["status"] == "complete"]
    if tasks:
        step(
            "research_history",
            "scripts.verify_research_gpu",
            [item for task in tasks for item in ["--task-id", task["id"]]],
        )
    else:
        steps.append({"name": "research_history", "passed": False, "error": "无完整历史任务"})

    native_scan = []
    for kind in ["native", "scan"]:
        start = time.monotonic()
        result = (
            client.post(
                "/api/chat",
                json={
                    "question": "Position-wise Feed-Forward Networks 的 inner-layer dimension 是多少？",
                    "document_ids": [ids[kind]],
                },
            )
            .raise_for_status()
            .json()
        )
        passed = not result["insufficient_evidence"] and "2048" in result["answer"]
        sources = []
        for source in result["citations"]:
            page = get(f"/api/documents/{source['document_id']}/pages/{source['page']}")
            same = "".join(source["quote"].split()) in "".join(page["text"].split())
            passed = passed and source["document_id"] == ids[kind] and source["page"] == 5 and same
            passed = passed and page["method"] == ("ocr" if kind == "scan" else "native")
            sources.append(
                {
                    "document_id": source["document_id"],
                    "page": source["page"],
                    "page_sha256": sha(page["text"].encode()),
                    "quote_sha256": sha(source["quote"].encode()),
                }
            )
        passed = bool(passed and sources)
        native_scan.append(
            {
                "kind": kind,
                "passed": passed,
                "answer": result["answer"],
                "seconds": round(time.monotonic() - start, 2),
                "sources": sources,
            }
        )
    (args.output_dir / "native_scan.json").write_text(
        json.dumps(native_scan, ensure_ascii=False, indent=2) + "\n"
    )
    steps.append({"name": "native_scan_qa", "passed": all(c["passed"] for c in native_scan)})

    after = get("/api/documents?include_archived=true")

    def choices(values):
        return sorted(
            (d["id"], d["status"], d["is_active"], d["trashed_at"], d["version"]) for d in values
        )

    unchanged = choices(before) == choices(after)
    report = {
        "verified_at": datetime.now(timezone.utc).isoformat(),
        "base_url": args.base_url,
        "isolated_library_asserted": True,
        "runner_sha256": sha(Path(__file__).read_bytes()),
        "health": health,
        "documents": len(before),
        "steps": steps,
        "document_status_and_version_choices_unchanged": unchanged,
        "passed": unchanged and all(s["passed"] for s in steps),
        "scope": "已有文献副本上的API联合回归；生成写作、摘要与部分翻译为真实本地推理，翻译可复用已通过缓存。研究任务为历史结果核对，扫描页沿用已有OCR结果，本轮未重新OCR。不是浏览器、外网或通用语义准确率验收。",
    }
    (args.output_dir / "SUMMARY.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n"
    )
    client.close()
    return report["passed"]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-url", default="http://127.0.0.1:18478")
    parser.add_argument("--isolated-library", action="store_true")
    parser.add_argument("--fixture-dir", required=True, type=Path)
    parser.add_argument("--gpt2-pdf", required=True, type=Path)
    parser.add_argument("--output-dir", required=True, type=Path)
    parser.add_argument(
        "--refresh-stale-history",
        action="store_true",
        help="仅在隔离副本中显式补生成失效或缺失的历史摘要，不重新检索或下载",
    )
    if not run(parser.parse_args()):
        raise SystemExit("存在失败，已保留本轮报告和日志")


if __name__ == "__main__":
    main()
