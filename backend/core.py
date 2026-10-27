"""按页切块、持久化与检索。索引不依赖第三方在线服务。"""

import hashlib
import math
import re
import sqlite3
from collections import Counter
from pathlib import Path


def tokens(text: str) -> list[str]:
    # 英文词 + 中文单字及双字，避免中文问题被当成一个长词。
    words = re.findall(r"[a-z0-9]+|[\u4e00-\u9fff]", text.lower())
    chinese = re.findall(r"[\u4e00-\u9fff]+", text)
    return words + [s[i : i + 2] for s in chinese for i in range(len(s) - 1)]


def chunks_for_page(text: str, page: int, size: int = 1100, overlap: int = 160):
    text = re.sub(r"[ \t]+", " ", text).strip()
    for start in range(0, len(text), size - overlap):
        part = text[start : start + size].strip()
        if part:
            yield {"page": page, "text": part, "offset": start}
        if start + size >= len(text):
            break


class Store:
    def __init__(self, root: Path):
        self.root = root
        root.mkdir(parents=True, exist_ok=True)
        with self.connect() as db:
            db.executescript("""
                PRAGMA journal_mode=WAL;
                CREATE TABLE IF NOT EXISTS documents (
                  id TEXT PRIMARY KEY, filename TEXT, title TEXT, pages INTEGER,
                  status TEXT, error TEXT, created TEXT DEFAULT CURRENT_TIMESTAMP
                );
                CREATE TABLE IF NOT EXISTS chunks (
                  id INTEGER PRIMARY KEY, document_id TEXT, page INTEGER,
                  offset INTEGER, text TEXT, vector BLOB
                );
                CREATE INDEX IF NOT EXISTS chunk_document ON chunks(document_id);
            """)

    def connect(self):
        db = sqlite3.connect(self.root / "library.sqlite3", timeout=30)
        db.row_factory = sqlite3.Row
        return db

    def documents(self):
        with self.connect() as db:
            return [dict(r) for r in db.execute("SELECT * FROM documents ORDER BY created DESC")]

    def document(self, doc_id):
        with self.connect() as db:
            row = db.execute("SELECT * FROM documents WHERE id=?", (doc_id,)).fetchone()
            return dict(row) if row else None

    def create(self, data: bytes, filename: str):
        doc_id = hashlib.sha256(data).hexdigest()
        with self.connect() as db:
            old = db.execute("SELECT * FROM documents WHERE id=?", (doc_id,)).fetchone()
            if old:
                return dict(old), True
            (self.root / (doc_id + ".pdf")).write_bytes(data)
            db.execute(
                "INSERT INTO documents(id,filename,title,pages,status,error) VALUES(?,?,?,0,'queued','')",
                (doc_id, filename, Path(filename).stem),
            )
        return self.document(doc_id), False

    def finish(self, doc_id, pages, chunks, title=None):
        with self.connect() as db:
            db.execute("DELETE FROM chunks WHERE document_id=?", (doc_id,))
            db.executemany(
                "INSERT INTO chunks(document_id,page,offset,text,vector) VALUES(?,?,?,?,?)",
                [(doc_id, c["page"], c["offset"], c["text"], c.get("vector")) for c in chunks],
            )
            db.execute(
                "UPDATE documents SET pages=?,status='ready',error='',title=COALESCE(?,title) WHERE id=?",
                (pages, title, doc_id),
            )

    def fail(self, doc_id, error):
        with self.connect() as db:
            db.execute(
                "UPDATE documents SET status='error',error=? WHERE id=?",
                (error, doc_id),
            )

    def rows(self, scope):
        with self.connect() as db:
            sql = "SELECT c.*, d.title FROM chunks c JOIN documents d ON c.document_id=d.id WHERE d.status='ready'"
            args = []
            if scope:
                sql += " AND document_id IN (" + ",".join("?" for _ in scope) + ")"
                args = scope
            return [dict(r) for r in db.execute(sql, args)]


def retrieve(rows, question, query_vector=None, limit=6):
    """BM25 与本地 E5 向量以 RRF 合并；不把检索分数当事实置信度。"""
    if not rows:
        return []
    terms = [Counter(tokens(r["text"])) for r in rows]
    lengths = [sum(c.values()) for c in terms]
    avg = sum(lengths) / len(lengths) or 1
    query = set(tokens(question))
    df = Counter(t for c in terms for t in c)
    scores = []
    for counts, length in zip(terms, lengths):
        score = 0.0
        for term in query:
            tf = counts[term]
            score += (
                math.log(1 + (len(rows) - df[term] + 0.5) / (df[term] + 0.5))
                * tf
                * 2.5
                / (tf + 1.5 * (0.25 + 0.75 * length / avg))
            )
        scores.append(score)
    sparse = sorted(
        (i for i, s in enumerate(scores) if s > 0),
        key=lambda i: scores[i],
        reverse=True,
    )[:40]
    ranks = {i: 1 / (60 + rank) for rank, i in enumerate(sparse, 1)}
    if query_vector is not None:
        import numpy as np

        dense = [
            (
                i,
                float(np.dot(np.frombuffer(r["vector"], dtype=np.float32), query_vector)),
            )
            for i, r in enumerate(rows)
            if r.get("vector")
        ]
        for rank, (i, _) in enumerate(sorted(dense, key=lambda x: x[1], reverse=True)[:40], 1):
            ranks[i] = ranks.get(i, 0) + 1 / (60 + rank)
    return [
        dict(rows[i], score=ranks[i]) for i in sorted(ranks, key=ranks.get, reverse=True)[:limit]
    ]


def validate_answer(value, evidence):
    """校验逐条结论的引用、原文摘录和数值；不等价于完整语义蕴含判断。"""
    if not isinstance(value, dict):
        raise ValueError("模型输出不是 JSON 对象")
    if value.get("insufficient_evidence") is True:
        return "当前检索到的原文不足以回答这个问题，请补充文献或换一种问法。", [], True
    claims = value.get("claims")
    if not isinstance(claims, list) or not 1 <= len(claims) <= 8:
        raise ValueError("回答缺少逐条结论")
    sources = {e["label"]: e for e in evidence}

    def normalize(text):
        return re.sub(r"\s+", "", text).lower()

    def numbers(text):
        return set(re.findall(r"\d+(?:\.\d+)?", text))

    answer, labels = [], []
    for claim in claims:
        if not isinstance(claim, dict):
            raise ValueError("结论字段类型错误")
        text, source = claim.get("text"), claim.get("source")
        quote = (
            claim.get("quote", sources.get(source, {}).get("text", ""))
            if isinstance(source, str)
            else ""
        )
        if not all(isinstance(v, str) for v in (text, source, quote)) or source not in sources:
            raise ValueError("模型引用了检索结果之外的来源")
        if not text.strip() or len(text) > 1400 or not 15 <= len(quote) <= 2000:
            raise ValueError("结论或摘录长度不正确")
        if normalize(quote) not in normalize(sources[source]["text"]):
            matches = [e for e in evidence if normalize(quote) in normalize(e["text"])]
            locations = {(e.get("document_id"), e.get("page")) for e in matches}
            if not matches or len(locations) != 1:
                raise ValueError("摘录无法唯一定位到原文")
            # 模型可能抄对原句却写错标号；按真实逐字匹配位置重新建立引用。
            source = matches[0]["label"]
            claim["source"] = source
        if not numbers(text).issubset(numbers(quote)):
            raise ValueError("结论包含原文摘录未支持的数值")
        claim["quote"] = quote
        answer.append(text.strip() + f"[{source}]")
        labels.append(source)
    return "\n\n".join(answer), list(dict.fromkeys(labels)), False
