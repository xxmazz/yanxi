"""只读检查已执行的真实研究任务；不额外下载或启动推理。"""

import argparse
import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path

import httpx

p = argparse.ArgumentParser(description=__doc__)
p.add_argument("--base-url", default="http://127.0.0.1:18474")
p.add_argument("--output", type=Path, required=True)
a = p.parse_args()
c = httpx.Client(base_url=a.base_url, trust_env=False, timeout=120)
tasks = c.get("/api/research/tasks").raise_for_status().json()
cases = []
for query in ["2005.11401v4", "1905.07830v1"]:
    task = next(t for t in tasks if t["query"] == query and t["status"] == "complete")
    assert task == c.get(f"/api/research/tasks/{task['id']}").raise_for_status().json()
    assert task["selected"] and task["results"]
    assert {s["document_id"] for s in task["selected"]} == {
        r["document_id"] for r in task["results"]
    }
    rows = []
    for item in task["results"]:
        doc_id = item["document_id"]
        pdf = c.get(item["pdf_url"]).raise_for_status().content
        assert hashlib.sha256(pdf).hexdigest() == doc_id
        sources = c.get(f"/api/documents/{doc_id}/sources").raise_for_status().json()
        assert any(s["arxiv_id"] == query for s in sources)
        rows.append({k: v for k, v in item.items() if k != "abstract"})
    cases.append(
        {
            "query": query,
            "status": task["status"],
            "attempt": task["attempt"],
            "events": task["events"],
            "results": rows,
            "model_summary_available": not task["summary"]["insufficient_evidence"],
        }
    )
assert c.get("/api/research/tasks/missing").status_code == 404
report = {
    "verified_at": datetime.now(timezone.utc).isoformat(),
    "cases": cases,
    "limitations": "真实单篇任务分别验证已入库 RAG 复用和新下载 HellaSwag 14页；本轮 CPU 无模型推理，摘要综述明确不可用。未完成多篇新下载和中文主题的 GPU 端到端回归。取消、故障恢复、版本确认及状态变化由隔离测试验证。",
}
a.output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n")
print([(r["query"], r["status"]) for r in cases])
