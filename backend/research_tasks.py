"""有界研究任务编排：保存检查点、复用导入队列、等待解析并汇总来源。"""

import asyncio
import json
import time
import uuid

ACTIVE = {"queued", "running"}
RETRYABLE = {"error", "interrupted", "needs_review"}


class ResearchTasks:
    def __init__(self, store):
        self.store = store
        self.running = {}
        with store.connect() as db:
            db.execute(
                "CREATE TABLE IF NOT EXISTS research_tasks(id TEXT PRIMARY KEY, payload TEXT NOT NULL)"
            )

    def save(self, task):
        task["updated_at"] = time.time()
        with self.store.connect() as db:
            db.execute(
                "INSERT OR REPLACE INTO research_tasks VALUES(?,?)",
                (task["id"], json.dumps(task, ensure_ascii=False)),
            )
        return task

    def get(self, task_id):
        with self.store.connect() as db:
            row = db.execute("SELECT payload FROM research_tasks WHERE id=?", (task_id,)).fetchone()
        if not row:
            raise KeyError("任务不存在")
        return json.loads(row["payload"])

    def list(self):
        with self.store.connect() as db:
            tasks = [
                json.loads(r["payload"]) for r in db.execute("SELECT payload FROM research_tasks")
            ]
        return sorted(tasks, key=lambda t: t["created_at"], reverse=True)[:20]

    def recover(self):
        for task in self.list():
            if task["status"] in ACTIVE:
                task.update(
                    status="interrupted", error="服务重启中断了任务，可从已保存的论文列表继续。"
                )
                self.save(task)

    def create(self, query, limit):
        if any(t["status"] in ACTIVE for t in self.list()):
            raise ValueError("已有研究任务正在运行，请等待完成或取消")
        return self.save(
            {
                "id": uuid.uuid4().hex,
                "query": query,
                "limit": limit,
                "status": "queued",
                "stage": "search",
                "error": "",
                "attempt": 1,
                "created_at": time.time(),
                "selected": [],
                "results": [],
                "summary": None,
                "events": [
                    {
                        "stage": "plan",
                        "message": f"检索主题 → 选择最多 {limit} 篇 → 逐篇入库并等待解析 → 汇总来源",
                        "time": time.time(),
                    }
                ],
            }
        )

    def retry(self, task_id):
        task = self.get(task_id)
        if task["status"] not in RETRYABLE:
            raise ValueError("此任务当前不能重试")
        if any(t["status"] in ACTIVE for t in self.list()):
            raise ValueError("已有研究任务正在运行")
        task.update(status="queued", error="", attempt=task["attempt"] + 1)
        return self.save(task)

    def cancel(self, task_id):
        task = self.get(task_id)
        if task["status"] in ACTIVE:
            task.update(
                status="cancelled",
                error="已停止后续步骤；已提交的单篇下载或解析将正常结束，文献保留。",
            )
            self.save(task)
        return task

    def start(self, task_id, **callbacks):
        if task_id in self.running:
            raise ValueError("任务仍在结束中，请稍后重试")
        future = asyncio.create_task(self.run(task_id, **callbacks))
        self.running[task_id] = future
        future.add_done_callback(lambda _: self.running.pop(task_id, None))

    async def shutdown(self):
        for future in list(self.running.values()):
            future.cancel()
        await asyncio.gather(*list(self.running.values()), return_exceptions=True)

    def event(self, task_id, stage, message, **changes):
        task = self.get(task_id)
        if task["status"] == "cancelled":
            raise asyncio.CancelledError()
        task.update(stage=stage, **changes)
        task["events"].append({"stage": stage, "message": message, "time": time.time()})
        self.save(task)
        return task

    async def run(self, task_id, search, submit, job, summarize, interval=2, timeout=1200):
        started = time.monotonic()
        try:
            task = self.event(task_id, "search", "读取检索检查点", status="running")
            if not task["selected"]:
                response = await search(task["query"])
                if response.get("stale"):
                    raise ValueError("检索只返回过期缓存，请在 arXiv 可用后重试")
                selected, seen = [], set()
                for item in response["items"]:
                    base = item.get("base_id", item["arxiv_id"])
                    if base in seen or (item.get("imported") or {}).get("archived"):
                        continue
                    seen.add(base)
                    selected.append(
                        {
                            "arxiv_id": item["arxiv_id"],
                            "title": item["title"],
                            "url": item["url"],
                            "reason": "arXiv 官方检索相关性顺序；同作品只保留本次首个候选",
                            "job_id": "",
                            "document_id": "",
                        }
                    )
                    if len(selected) == task["limit"]:
                        break
                if not selected:
                    raise ValueError("没有可入库的检索结果，请修改主题后建立新任务")
                task = self.event(
                    task_id,
                    "select",
                    f"已保存 {len(selected)} 篇候选，后续重试复用此范围",
                    selected=selected,
                    search_query=response.get("search_query", task["query"]),
                )
            for index, item in enumerate(task["selected"]):
                self.event(task_id, "import", f"处理 {item['title']}")
                if not item["document_id"]:
                    existing_job = job(item["job_id"]) if item["job_id"] else None
                    if not existing_job or existing_job["status"] == "error":
                        submitted = await submit(item["arxiv_id"])
                        task = self.get(task_id)
                        task["selected"][index]["job_id"] = submitted["id"]
                        task = self.save(task)
                        item = task["selected"][index]
                    while True:
                        self.check(task_id, started, timeout)
                        current = job(item["job_id"])
                        if not current or current["status"] == "error":
                            raise ValueError(current["error"] if current else "下载记录不存在")
                        if current["status"] == "complete":
                            task = self.get(task_id)
                            task["selected"][index]["document_id"] = current["document_id"]
                            task = self.save(task)
                            item = task["selected"][index]
                            break
                        await asyncio.sleep(interval)
                self.event(task_id, "parse", "等待正文解析、索引与版本确认")
                while True:
                    self.check(task_id, started, timeout)
                    doc = self.store.document(item["document_id"])
                    if not doc or doc["trashed_at"] or not doc["is_active"]:
                        raise ValueError("所选论文已归档或被替换，请在文献库确认后重试")
                    if doc["status"] == "review":
                        self.event(
                            task_id,
                            "parse",
                            "发现相似版本，请先到文献库确认保留策略",
                            status="needs_review",
                        )
                        return
                    if doc["status"] == "error":
                        raise ValueError("论文解析失败，请在文献库重新解析后重试：" + doc["error"])
                    if doc["status"] == "ready":
                        break
                    await asyncio.sleep(interval)
            task = self.get(task_id)
            ids = [item["document_id"] for item in task["selected"]]
            results = []
            for doc_id in ids:
                doc = self.store.document(doc_id)
                results.append(
                    {
                        "document_id": doc_id,
                        "title": doc["title"],
                        "pages": doc["pages"],
                        "abstract": doc["abstract"],
                        "pdf_url": f"/api/documents/{doc_id}/pdf",
                    }
                )
            self.event(
                task_id,
                "summarize",
                "已解析文献来源汇总；检查本地模型是否可生成摘要综述",
                results=results,
            )
            summary = await summarize(ids)
            self.check(task_id, started, timeout)
            for doc_id in ids:
                current = self.store.document(doc_id)
                if (
                    not current
                    or current["status"] != "ready"
                    or current["trashed_at"]
                    or not current["is_active"]
                ):
                    raise ValueError("汇总期间文献状态发生变化，请确认文献库后重试")
            self.event(
                task_id,
                "complete",
                "任务完成，原文与来源可回看",
                status="complete",
                summary=summary,
            )
        except asyncio.CancelledError:
            task = self.get(task_id)
            if task["status"] != "cancelled":
                task.update(status="interrupted", error="服务停止中断任务，可从检查点继续")
                self.save(task)
        except Exception as exc:
            task = self.get(task_id)
            if task["status"] != "cancelled":
                task.update(status="error", error=str(exc)[:400])
                self.save(task)

    def check(self, task_id, started, timeout):
        if self.get(task_id)["status"] == "cancelled":
            raise asyncio.CancelledError()
        if time.monotonic() - started > timeout:
            raise ValueError("任务超过本次运行时间限制，可从检查点重试")
