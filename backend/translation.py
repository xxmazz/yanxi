"""本地翻译任务、逐段缓存与原文对齐；不改写文献或问答索引。"""

import hashlib
import json
import re
import threading
import uuid
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

VERSION = "academic-v2"
PROTECTED = re.compile(
    r"```[\s\S]*?```|\$\$[\s\S]*?\$\$|\\\[[\s\S]*?\\\]|"
    r"\\\([\s\S]*?\\\)|`[^`\n]+`|\$(?:[^$\n]|\\\$)+\$|https?://[^\s<>]+|"
    r"\[\s*\d+(?:\s*[,;–—-]\s*\d+)*\s*\]|(?im:^arXiv:[^\n]+$)|"
    r"\([^()]*\bet\s*(?:al\.?)?\s*$"
)
MARKER = re.compile(r"ZXQKEEP\d{4,}QXZ")
NUMBERS = re.compile(r"(?:(?<![A-Za-z\d])[-+])?\d+(?:[.,]\d+)*(?:[eE][-+]?\d+)?%?")


def digest(text):
    return hashlib.sha256(text.encode()).hexdigest()


def protect(text, numbers=True):
    if MARKER.search(text):
        raise ValueError("原文包含翻译保留标记，请调整该段文本")
    saved = {}

    def replace(match):
        key = f"ZXQKEEP{len(saved):04d}QXZ"
        saved[key] = match.group()
        return key

    text = PROTECTED.sub(replace, text)
    if numbers:
        # 避免小模型变更小数、超参数、年份及文内引用编号。
        text = "".join(
            part if MARKER.fullmatch(part) else NUMBERS.sub(replace, part)
            for part in re.split(r"(ZXQKEEP\d{4,}QXZ)", text)
        )
    return text, saved


def restore(text, saved):
    if set(MARKER.findall(text)) != set(saved) or any(text.count(k) != 1 for k in saved):
        raise ValueError("译文未保留全部公式、代码或数值，本段未通过校验，请重试")
    return MARKER.sub(lambda m: saved[m.group()], text)


def segments(text, limit=900):
    # 在已保护的完整公式/代码之外切段，原文拼接后必须逐字等于页面正文。
    masked, saved = protect(text, numbers=False)
    parts, current = [], ""
    for word in re.findall(r"\S+\s*|\s+", masked):
        if current and (len(current) + len(word) > limit or current.endswith("\n\n")):
            parts.append(current)
            current = ""
        current += word
    if current:
        parts.append(current)
    return [MARKER.sub(lambda m: saved[m.group()], part) for part in parts]


def canonical_numbers(result, expected):
    """只恢复唯一匹配的 0..99 中文整数写法；不猜测缺失小数或更改数值。"""
    actual = Counter(NUMBERS.findall(MARKER.sub("", result)))
    if actual - expected:
        return result
    digits = "零一二三四五六七八九"
    for value, count in (expected - actual).items():
        if not re.fullmatch(r"0|[1-9]\d?", value):
            continue
        number = int(value)
        chinese = (
            digits[number]
            if number < 10
            else (
                (digits[number // 10] if number >= 20 else "")
                + "十"
                + (digits[number % 10] if number % 10 else "")
            )
        )
        pattern = (
            r"(?<![零〇一二三四五六七八九十百千万两])"
            + chinese
            + r"(?![零〇一二三四五六七八九十百千万两])"
        )
        if len(re.findall(pattern, result)) == count:
            result = re.sub(pattern, value, result)
    return result


def translate_segment(source, target, models, depth=0):
    if depth and re.fullmatch(r"[A-Za-z_ηθ0-9()|,^−\-]{1,40}", source.strip()):
        return source
    front = re.match(r"([\s\S]{20,}?\n(?:Abstract|摘要)\s*\n)([\s\S]+)", source)
    if front and ("@" in front[1] or re.search(r"University|Institute|†|‡", front[1])):
        return front[1] + translate_segment(front[2], target, models, depth)
    # 多条正式书目信息保留原文，避免翻译作者名、刊名或引用年份。
    if (
        len(re.findall(r"\b(?:19|20)\d{2}\.", source)) >= 2
        and len(re.findall(r"\b(?:Proceedings|CoRR|Conference|Journal|OpenReview|arXiv)\b", source))
        >= 1
    ):
        return source
    masked, saved = protect(source, numbers=False)
    if not re.search(r"[A-Za-z\u4e00-\u9fff]", MARKER.sub("", masked)):
        return source
    if len(masked) > 4500:
        raise ValueError("此段过长，请使用局部翻译")
    try:
        result = models.translate(masked, target)
        if not isinstance(result, str) or not result.strip():
            raise ValueError("本地模型返回空译文")
        expected = Counter(NUMBERS.findall(MARKER.sub("", masked)))
        if target == "zh":
            result = canonical_numbers(result, expected)
        translated = restore(result.strip(), saved)
        actual = Counter(NUMBERS.findall(MARKER.sub("", result)))
        if expected != actual:
            raise ValueError(
                f"译文数值与原文不一致：原文 {dict(expected)}，译文 {dict(actual)}；本段未保存"
            )
    except ValueError:
        literal_text, literal_values = protect(source, numbers=True)
        if literal_values and depth < 2:
            # 若模型省略占位符，将受保护内容独立原样拼回，只翻译其间正文。
            # 这比猜测被删掉的公式／引用应该插在哪里更可靠。
            parts = re.split(r"(ZXQKEEP\d{4,}QXZ)", literal_text)
            return "".join(
                literal_values[part]
                if part in literal_values
                else translate_segment(part, target, models, depth + 1)
                if part.strip()
                else part
                for part in parts
            )
        # 图中标签与密集引用易被小模型省略；只在校验失败时缩短上下文重译。
        parts = segments(source, max(140, len(masked) // 2))
        if depth >= 2 or len(parts) <= 1:
            raise
        return "\n".join(translate_segment(part, target, models, depth + 1) for part in parts)
    if target == "zh" and len(masked) > 100 and re.search(r"[A-Za-z]{3,}", MARKER.sub("", masked)):
        if not re.search(r"[\u4e00-\u9fff]", translated):
            raise ValueError("本地模型没有返回中文译文，请重试")
    if target == "en" and len(re.findall(r"[\u4e00-\u9fff]", translated)) > 5:
        raise ValueError("本地模型没有完整返回英文译文，请重试")
    return translated


class TranslationService:
    def __init__(self, store, models):
        self.store, self.models = store, models
        self.version = VERSION + ":" + Path(models.llm_path).name
        self.workers = ThreadPoolExecutor(max_workers=1, thread_name_prefix="translation")
        self.slots = threading.BoundedSemaphore(2)
        self.lock = threading.Lock()
        with store.connect() as db:
            db.executescript("""
                CREATE TABLE IF NOT EXISTS translations (
                  document_id TEXT, page INTEGER, target TEXT, source_hash TEXT,
                  version TEXT, segments TEXT, updated TEXT DEFAULT CURRENT_TIMESTAMP,
                  PRIMARY KEY(document_id,page,target,source_hash,version)
                );
                CREATE TABLE IF NOT EXISTS translation_jobs (
                  id TEXT PRIMARY KEY, document_id TEXT, target TEXT, pages TEXT,
                  status TEXT, completed INTEGER DEFAULT 0, total INTEGER DEFAULT 0,
                  error TEXT DEFAULT '', cancel_requested INTEGER DEFAULT 0,
                  source_text TEXT DEFAULT '', result TEXT DEFAULT '',
                  created TEXT DEFAULT CURRENT_TIMESTAMP, updated TEXT DEFAULT CURRENT_TIMESTAMP
                );
            """)

    def recover(self):
        with self.store.connect() as db:
            db.execute("""UPDATE translation_jobs SET status='error',error='服务重启中断任务，可重新发起以复用已完成段落',
                       updated=CURRENT_TIMESTAMP WHERE status IN ('queued','running')""")

    def shutdown(self):
        with self.store.connect() as db:
            db.execute(
                "UPDATE translation_jobs SET cancel_requested=1 WHERE status IN ('queued','running')"
            )
        self.workers.shutdown(wait=True)

    def job(self, job_id):
        with self.store.connect() as db:
            row = db.execute("SELECT * FROM translation_jobs WHERE id=?", (job_id,)).fetchone()
        if row is None:
            raise KeyError("翻译任务不存在")
        row = dict(row)
        row["pages"] = json.loads(row["pages"])
        return row

    def update(self, job_id, **fields):
        with self.store.connect() as db:
            db.execute(
                "UPDATE translation_jobs SET "
                + ",".join(f"{key}=?" for key in fields)
                + ",updated=CURRENT_TIMESTAMP WHERE id=?",
                (*fields.values(), job_id),
            )

    def check_document(self, doc_id):
        doc = self.store.document(doc_id)
        if not doc:
            raise KeyError("文献不存在")
        if doc["status"] != "ready" or doc["trashed_at"] or not doc["is_active"]:
            raise ValueError("请先完成解析与版本确认，并恢复需要阅读的文献")
        return doc

    def page(self, doc_id, number, target):
        doc = self.check_document(doc_id)
        page = self.store.page(doc_id, number)
        if number < 1 or number > doc["pages"] or page is None:
            raise KeyError("此页尚未解析或不存在")
        source_hash = digest(page["text"])
        with self.store.connect() as db:
            row = db.execute(
                """SELECT segments,updated FROM translations WHERE document_id=? AND page=?
                AND target=? AND source_hash=? AND version=?""",
                (doc_id, number, target, source_hash, self.version),
            ).fetchone()
        items = (
            json.loads(row["segments"])
            if row
            else [{"source": part, "translation": None} for part in segments(page["text"])]
        )
        return {
            "page": number,
            "target": target,
            "source_hash": source_hash,
            "method": page["method"],
            "segments": items,
            "complete": all(item["translation"] is not None for item in items),
            "updated": row["updated"] if row else None,
            "version": self.version,
        }

    def save_page(self, doc_id, page, target):
        with self.store.connect() as db:
            db.execute(
                "INSERT OR REPLACE INTO translations VALUES(?,?,?,?,?,?,CURRENT_TIMESTAMP)",
                (
                    doc_id,
                    page["page"],
                    target,
                    page["source_hash"],
                    self.version,
                    json.dumps(page["segments"], ensure_ascii=False),
                ),
            )

    def start(self, target, doc_id="", number=None, text=""):
        with self.lock:
            pages = []
            snapshots = []
            if doc_id:
                doc = self.check_document(doc_id)
                pages = [number] if number is not None else list(range(1, doc["pages"] + 1))
                for n in pages:
                    snapshots.append(self.page(doc_id, n, target))
            with self.store.connect() as db:
                active = db.execute(
                    """SELECT id FROM translation_jobs WHERE document_id=? AND target=? AND pages=?
                    AND source_text=? AND status IN ('queued','running') AND cancel_requested=0""",
                    (doc_id, target, json.dumps(pages), text),
                ).fetchone()
            if active:
                return self.job(active["id"])
            # 已完成译文不占 GPU 队列；长篇任务运行时仍能立即复用与导出。
            if snapshots and all(page["complete"] for page in snapshots):
                job_id = uuid.uuid4().hex
                count = sum(len(page["segments"]) for page in snapshots)
                with self.store.connect() as db:
                    db.execute(
                        """INSERT INTO translation_jobs(id,document_id,target,pages,status,completed,total)
                        VALUES(?,?,?,?,'ready',?,?)""",
                        (job_id, doc_id, target, json.dumps(pages), count, count),
                    )
                return self.job(job_id)
            if self.models.llm is None:
                raise RuntimeError("本地翻译模型未就绪")
            if not self.slots.acquire(blocking=False):
                raise OverflowError("翻译队列已满，请稍后重试")
            job_id = uuid.uuid4().hex
            try:
                with self.store.connect() as db:
                    db.execute(
                        """INSERT INTO translation_jobs(id,document_id,target,pages,status,source_text)
                        VALUES(?,?,?,?,'queued',?)""",
                        (job_id, doc_id, target, json.dumps(pages), text),
                    )
                self.workers.submit(self.run, job_id)
            except Exception:
                self.slots.release()
                raise
            return self.job(job_id)

    def cancel(self, job_id):
        job = self.job(job_id)
        if job["status"] in ("queued", "running"):
            self.update(job_id, cancel_requested=1)
        return self.job(job_id)

    def run(self, job_id):
        try:
            job = self.job(job_id)
            target, doc_id = job["target"], job["document_id"]
            pages = (
                [self.page(doc_id, n, target) for n in job["pages"]]
                if doc_id
                else [
                    {
                        "segments": [
                            {"source": s, "translation": None} for s in segments(job["source_text"])
                        ]
                    }
                ]
            )
            total = sum(len(page["segments"]) for page in pages)
            completed = 0
            self.update(job_id, status="running", total=total)
            for page in pages:
                for item in page["segments"]:
                    if self.job(job_id)["cancel_requested"]:
                        self.update(job_id, status="cancelled")
                        return
                    if doc_id:
                        self.check_document(doc_id)
                        current = self.store.page(doc_id, page["page"])
                        if not current or digest(current["text"]) != page["source_hash"]:
                            raise ValueError("原文已重新解析，请重新发起翻译")
                    if item["translation"] is None:
                        item["translation"] = translate_segment(item["source"], target, self.models)
                        if doc_id:
                            self.save_page(doc_id, page, target)
                    completed += 1
                    self.update(job_id, completed=completed)
            if doc_id:
                self.update(job_id, status="ready")
            else:
                self.update(
                    job_id,
                    status="ready",
                    result="\n\n".join(item["translation"] for item in pages[0]["segments"]),
                )
        except Exception as exc:
            self.update(job_id, status="error", error=str(exc)[:300])
        finally:
            self.slots.release()

    def export(self, doc_id, target):
        doc = self.check_document(doc_id)
        output = [
            f"# {doc['title']}",
            "本地模型译文，须结合原文核对；按 PDF 物理页与正文段落对应。",
        ]
        for number in range(1, doc["pages"] + 1):
            page = self.page(doc_id, number, target)
            if not page["complete"]:
                raise ValueError(f"第 {number} 页尚未译完，请先完成整篇翻译")
            output.append(f"## 物理第 {number} 页")
            for index, item in enumerate(page["segments"], 1):
                output.extend(
                    [f"### 段落 {index} · 原文", item["source"], "译文：", item["translation"]]
                )
        return "\n\n".join(output)
