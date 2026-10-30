"""持久化论文订阅与参考文献批量入库；复用官方检索和现有下载队列。"""

import asyncio
import json
import re
import time
import uuid

from .discovery import ARXIV_ID


def explicit_ids(text):
    marked = re.findall(r"(?:arxiv\s*:\s*|arxiv\.org/(?:abs|pdf)/)(" + ARXIV_ID + r")", text, re.I)
    # CoRR 书目中 abs/ 后的完整编号也是明确身份；普通编号不按arXiv猜测。
    corr = re.findall(r"\bCoRR\s*,\s*abs/(" + ARXIV_ID + r")", text, re.I)
    return marked + corr


def reference_candidates(data):
    items, seen, identified = [], set(), []
    for entry in data["entries"]:
        text = " ".join(e["quote"] for e in entry["evidence"])
        ids = explicit_ids(text)
        if ids:
            identified.append(entry["label"])
        for identifier in ids:
            base = re.sub(r"v\d+$", "", identifier)
            if base in seen:
                continue
            seen.add(base)
            items.append(
                {
                    "arxiv_id": identifier,
                    "reference": entry["label"],
                    "quote": text[:1800],
                    "pages": sorted({e["page"] for e in entry["evidence"]}),
                }
            )
    return {
        "items": items,
        "references": len(data["entries"]),
        "unresolved": len(data["entries"]) - len(identified),
        "identified_references": identified,
        "fingerprint": data["fingerprint"],
    }


class Following:
    def __init__(self, store):
        self.store = store
        self.running = {}
        with store.connect() as db:
            db.execute(
                "CREATE TABLE IF NOT EXISTS following(id TEXT PRIMARY KEY, kind TEXT, payload TEXT)"
            )

    def save(self, item):
        with self.store.connect() as db:
            db.execute(
                "INSERT OR REPLACE INTO following VALUES(?,?,?)",
                (item["id"], item["kind"], json.dumps(item, ensure_ascii=False)),
            )
        return item

    def get(self, identifier):
        with self.store.connect() as db:
            row = db.execute("SELECT payload FROM following WHERE id=?", (identifier,)).fetchone()
        if not row:
            raise KeyError("订阅或批量任务不存在")
        return json.loads(row[0])

    def list(self, kind):
        with self.store.connect() as db:
            return [
                json.loads(r[0])
                for r in db.execute(
                    "SELECT payload FROM following WHERE kind=? ORDER BY rowid DESC LIMIT 30",
                    (kind,),
                )
            ]

    def recover(self):
        for item in self.list("batch"):
            if item["status"] in {"queued", "running"}:
                item.update(status="interrupted", error="服务重启，可从下载检查点继续")
                self.save(item)

    def subscribe(self, query, cadence):
        if len(self.list("subscription")) >= 10:
            raise ValueError("最多10个订阅，请停用或修改现有订阅")
        return self.save(
            {
                "id": uuid.uuid4().hex,
                "kind": "subscription",
                "query": query.strip(),
                "cadence": cadence,
                "enabled": True,
                "next_run": time.time(),
                "last_run": None,
                "error": "",
                "items": [],
                "feedback": {},
                "seen": [],
            }
        )

    def configure(self, identifier, enabled):
        item = self.get(identifier)
        if item["kind"] != "subscription":
            raise ValueError("不是论文订阅")
        item["enabled"] = enabled
        if enabled:
            item["next_run"] = time.time()
        return self.save(item)

    async def refresh(self, identifier, search):
        if identifier in self.running:
            raise ValueError("该订阅正在刷新")
        item = self.get(identifier)
        if item["kind"] != "subscription":
            raise ValueError("不是论文订阅")
        self.running[identifier] = asyncio.current_task()
        try:
            response = await search(item["query"])
            if response.get("stale"):
                raise ValueError("官方查询失败，旧缓存不计作本次新推荐")
            current = self.get(identifier)
            old = set(current["seen"])
            records = []
            for candidate in response["items"]:
                base = candidate["base_id"]
                related = candidate.get("related_document")
                records.append(
                    {
                        **candidate,
                        "new_in_feed": base not in old,
                        "reason": f"与库内《{related['title']}》摘要主题接近（E5余弦{related['similarity']:.3f}）"
                        if related
                        else "匹配订阅关键词，按arXiv最新发布时间返回；未进行库内语义重排",
                    }
                )
            current.update(
                items=records,
                last_run=time.time(),
                error="",
                seen=list(dict.fromkeys([c["base_id"] for c in records] + current["seen"]))[:500],
                search_query=response.get("search_query"),
                cached=response.get("cached", False),
            )
            self.save(current)
        except Exception as exc:
            current = self.get(identifier)
            current["error"] = str(exc)[:300]
            self.save(current)
        finally:
            current = self.get(identifier)
            # 失败后至少15分钟再试；不将旧结果时间改写为成功刷新。
            delay = (
                900 if current["error"] else (86400 if current["cadence"] == "daily" else 7 * 86400)
            )
            current["next_run"] = time.time() + delay
            self.save(current)
            self.running.pop(identifier, None)
        return self.get(identifier)

    def feedback(self, identifier, base_id, value):
        item = self.get(identifier)
        if item["kind"] != "subscription" or base_id not in {p["base_id"] for p in item["items"]}:
            raise ValueError("推荐论文不存在")
        item["feedback"][base_id] = value
        return self.save(item)

    def batch(self, source_id, candidates):
        if any(b["status"] in {"queued", "running"} for b in self.list("batch")):
            raise ValueError("已有参考文献批量任务，请等待或取消")
        return self.save(
            {
                "id": uuid.uuid4().hex,
                "kind": "batch",
                "source_id": source_id,
                "status": "queued",
                "error": "",
                "items": [
                    {**c, "job_id": "", "document_id": "", "status": "pending", "error": ""}
                    for c in candidates
                ],
                "created": time.time(),
            }
        )

    def cancel(self, identifier):
        item = self.get(identifier)
        if item["kind"] != "batch":
            raise ValueError("不是批量任务")
        if item["status"] in {"running", "queued"}:
            item["status"] = "cancelled"
        return self.save(item)

    def start_batch(self, identifier, submit, job):
        if identifier in self.running:
            raise ValueError("批量任务仍在结束中")
        item = self.get(identifier)
        if item["kind"] != "batch" or item["status"] not in {
            "queued",
            "interrupted",
            "partial",
            "cancelled",
        }:
            raise ValueError("此任务不能继续")
        if any(
            b["id"] != identifier and b["status"] in {"queued", "running"}
            for b in self.list("batch")
        ):
            raise ValueError("已有其他批量任务")
        item["status"] = "running"
        self.save(item)
        task = asyncio.create_task(self.run_batch(identifier, submit, job))
        self.running[identifier] = task
        task.add_done_callback(lambda _: self.running.pop(identifier, None))

    async def run_batch(self, identifier, submit, job, interval=2):
        item = self.get(identifier)
        try:
            for index, entry in enumerate(item["items"]):
                if entry["status"] == "complete":
                    continue
                if self.get(identifier)["status"] == "cancelled":
                    return
                try:
                    current = job(entry["job_id"]) if entry["job_id"] else None
                    if not current or current["status"] == "error":
                        current = await submit(entry["arxiv_id"])
                        entry.update(job_id=current["id"], error="", status="downloading")
                        item["items"][index] = entry
                        self.save(item)
                    deadline = time.monotonic() + 420
                    while current["status"] in {"queued", "downloading"}:
                        await asyncio.sleep(interval)
                        if self.get(identifier)["status"] == "cancelled":
                            return
                        if time.monotonic() > deadline:
                            raise ValueError("单篇下载等待超时；继续时复用现有任务")
                        current = job(entry["job_id"])
                        if not current:
                            raise ValueError("下载记录不存在")
                    if current["status"] == "error":
                        raise ValueError(current["error"])
                    entry.update(status="complete", document_id=current["document_id"], error="")
                except Exception as exc:
                    entry.update(status="error", error=str(exc)[:300])
                item = self.get(identifier)
                if item["status"] == "cancelled":
                    return
                item["items"][index] = entry
                self.save(item)
            item["status"] = (
                "complete" if all(e["status"] == "complete" for e in item["items"]) else "partial"
            )
            self.save(item)
        except asyncio.CancelledError:
            item = self.get(identifier)
            if item["status"] != "cancelled":
                item["status"] = "interrupted"
                self.save(item)
            raise

    async def scheduler(self, search):
        while True:
            for item in self.list("subscription"):
                if (
                    item["enabled"]
                    and item["next_run"] <= time.time()
                    and item["id"] not in self.running
                ):
                    await self.refresh(item["id"], search)
            await asyncio.sleep(60)

    async def shutdown(self):
        tasks = set(self.running.values())
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
