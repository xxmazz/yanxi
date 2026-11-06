"""按页切块、持久化与检索。索引不依赖第三方在线服务。"""

import hashlib
import json
import math
import re
import sqlite3
from collections import Counter
from pathlib import Path

from .comparison import comparison_priority, comparison_topic, expand_comparison_evidence


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
                CREATE TABLE IF NOT EXISTS pages (
                  document_id TEXT, page INTEGER, text TEXT, method TEXT,
                  width REAL, height REAL, blocks TEXT, warning TEXT,
                  PRIMARY KEY(document_id,page)
                );
                CREATE TABLE IF NOT EXISTS version_decisions (
                  incoming_id TEXT, existing_id TEXT, action TEXT,
                  created TEXT DEFAULT CURRENT_TIMESTAMP
                );
            """)
            columns = {r["name"] for r in db.execute("PRAGMA table_info(documents)")}
            additions = {
                "processed_pages": "INTEGER DEFAULT 0",
                "ocr_pages": "INTEGER DEFAULT 0",
                "authors": "TEXT DEFAULT ''",
                "abstract": "TEXT DEFAULT ''",
                "arxiv_id": "TEXT DEFAULT ''",
                "version": "INTEGER DEFAULT 0",
                "profile": "TEXT DEFAULT '{}'",
                "content_vector": "BLOB",
                "similar_documents": "TEXT DEFAULT '[]'",
                "warnings": "TEXT DEFAULT '[]'",
                "trashed_at": "TEXT",
                "is_active": "INTEGER DEFAULT 1",
                "superseded_by": "TEXT",
            }
            for name, definition in additions.items():
                if name not in columns:
                    db.execute(f"ALTER TABLE documents ADD COLUMN {name} {definition}")

    def connect(self):
        db = sqlite3.connect(self.root / "library.sqlite3", timeout=30)
        db.row_factory = sqlite3.Row
        return db

    @staticmethod
    def public(row):
        if row is None:
            return None
        result = dict(row)
        result.pop("profile", None)
        result.pop("content_vector", None)
        for field in ("similar_documents", "warnings"):
            result[field] = json.loads(result[field] or "[]")
        return result

    def documents(self, include_archived=False):
        with self.connect() as db:
            where = "" if include_archived else " WHERE trashed_at IS NULL AND is_active=1"
            return [
                self.public(r)
                for r in db.execute(
                    "SELECT * FROM documents" + where + " ORDER BY created DESC, rowid DESC"
                )
            ]

    def document(self, doc_id):
        with self.connect() as db:
            return self.public(
                db.execute("SELECT * FROM documents WHERE id=?", (doc_id,)).fetchone()
            )

    def create(self, data: bytes, filename: str):
        doc_id = hashlib.sha256(data).hexdigest()
        with self.connect() as db:
            old = db.execute("SELECT * FROM documents WHERE id=?", (doc_id,)).fetchone()
            if old:
                return self.public(old), True
            (self.root / (doc_id + ".pdf")).write_bytes(data)
            db.execute(
                "INSERT INTO documents(id,filename,title,pages,status,error) VALUES(?,?,?,0,'queued','')",
                (doc_id, filename, Path(filename).stem),
            )
        return self.document(doc_id), False

    def begin(self, doc_id, count):
        with self.connect() as db:
            db.execute("DELETE FROM pages WHERE document_id=?", (doc_id,))
            db.execute(
                "UPDATE documents SET status='processing',pages=?,processed_pages=0,ocr_pages=0,error='' WHERE id=?",
                (count, doc_id),
            )

    def save_page(self, doc_id, number, page):
        with self.connect() as db:
            db.execute(
                "INSERT OR REPLACE INTO pages VALUES(?,?,?,?,?,?,?,?)",
                (
                    doc_id,
                    number,
                    page["text"],
                    page["method"],
                    page["width"],
                    page["height"],
                    json.dumps(page["blocks"], ensure_ascii=False),
                    page.get("warning", ""),
                ),
            )
            db.execute(
                "UPDATE documents SET processed_pages=?,ocr_pages=ocr_pages+? WHERE id=?",
                (number, int(page["method"] == "ocr"), doc_id),
            )

    def page(self, doc_id, number):
        with self.connect() as db:
            row = db.execute(
                "SELECT * FROM pages WHERE document_id=? AND page=?", (doc_id, number)
            ).fetchone()
            return dict(row) if row else None

    def analyze(self, doc_id, text, chunks, metadata):
        from .similarity import compare_documents, content_profile, identity, mean_vector

        incoming = {**metadata, "profile": content_profile(text), "vector": mean_vector(chunks)}
        matches = []
        with self.connect() as db:
            candidates = db.execute(
                """SELECT d.* FROM documents d WHERE d.id!=?
                AND d.status IN ('ready','queued','processing') AND d.trashed_at IS NULL AND d.is_active=1
                AND EXISTS(SELECT 1 FROM chunks c WHERE c.document_id=d.id)""",
                (doc_id,),
            ).fetchall()
        for row in candidates:
            existing = dict(row)
            existing["profile"] = json.loads(existing["profile"] or "{}")
            existing["vector"] = existing["content_vector"]
            if not existing["profile"].get("count"):
                with self.connect() as db:
                    old_chunks = [
                        dict(c)
                        for c in db.execute(
                            "SELECT c.*, d.title FROM chunks c JOIN documents d ON d.id=c.document_id WHERE d.id=? ORDER BY c.page,c.offset",
                            (existing["id"],),
                        )
                    ]
                existing["profile"] = content_profile("\n".join(c["text"] for c in old_chunks))
                existing["vector"] = mean_vector(old_chunks)
                first_page = "\n".join(c["text"] for c in old_chunks if c["page"] == 1)
                existing["arxiv_id"], existing["version"] = identity(first_page)
            match = compare_documents(incoming, existing)
            if match:
                matches.append(match)
        incoming["matches"] = sorted(matches, key=lambda m: m["overlap"], reverse=True)
        return incoming

    def finish(
        self, doc_id, pages, chunks, title=None, analysis=None, metadata=None, warnings=None
    ):
        metadata = metadata or {}
        analysis = analysis or {}
        matches = analysis.get("matches", [])
        with self.connect() as db:
            db.execute("DELETE FROM chunks WHERE document_id=?", (doc_id,))
            db.executemany(
                "INSERT INTO chunks(document_id,page,offset,text,vector) VALUES(?,?,?,?,?)",
                [(doc_id, c["page"], c["offset"], c["text"], c.get("vector")) for c in chunks],
            )
            db.execute(
                """UPDATE documents SET pages=?,processed_pages=?,status=?,error='',title=COALESCE(?,title),
                       authors=?,abstract=?,arxiv_id=?,version=?,profile=?,content_vector=?,similar_documents=?,warnings=? WHERE id=?""",
                (
                    pages,
                    pages,
                    "review" if matches else "ready",
                    title,
                    metadata.get("authors", ""),
                    metadata.get("abstract", ""),
                    metadata.get("arxiv_id", ""),
                    metadata.get("version", 0),
                    json.dumps(analysis.get("profile", {})),
                    analysis.get("vector"),
                    json.dumps(matches, ensure_ascii=False),
                    json.dumps(warnings or [], ensure_ascii=False),
                    doc_id,
                ),
            )

    def fail(self, doc_id, error):
        with self.connect() as db:
            db.execute("UPDATE documents SET status='error',error=? WHERE id=?", (error, doc_id))

    def queue(self, doc_id):
        with self.connect() as db:
            row = db.execute("SELECT * FROM documents WHERE id=?", (doc_id,)).fetchone()
            if not row:
                raise KeyError("文献不存在")
            if (
                row["status"] in ("queued", "processing")
                or row["trashed_at"]
                or not row["is_active"]
            ):
                raise ValueError("文献正在解析或已归档，请稍后重试或先恢复")
            db.execute("UPDATE documents SET status='queued',error='' WHERE id=?", (doc_id,))

    def trash(self, doc_id, restore=False):
        with self.connect() as db:
            row = db.execute("SELECT * FROM documents WHERE id=?", (doc_id,)).fetchone()
            if not row:
                raise KeyError("文献不存在")
            if row["status"] in ("queued", "processing"):
                raise ValueError("解析进行中，请等待完成后再操作")
            if restore:
                # review 文献仍需确认版本，不因恢复而绕过冲突检查。
                db.execute(
                    "UPDATE documents SET trashed_at=NULL,is_active=1,superseded_by=NULL WHERE id=?",
                    (doc_id,),
                )
            else:
                db.execute(
                    "UPDATE documents SET trashed_at=CURRENT_TIMESTAMP WHERE id=?", (doc_id,)
                )
        return self.document(doc_id)

    def resolve(self, doc_id, existing_id, action):
        if action not in ("keep_both", "keep_existing", "replace_existing"):
            raise ValueError("未知的版本处理方式")
        with self.connect() as db:
            # 同一事务内检查并更新，避免两次并发决策修改同一版本。
            db.execute("BEGIN IMMEDIATE")
            incoming = db.execute("SELECT * FROM documents WHERE id=?", (doc_id,)).fetchone()
            existing = db.execute("SELECT * FROM documents WHERE id=?", (existing_id,)).fetchone()
            if not incoming or not existing:
                raise KeyError("文献不存在")
            choices = json.loads(incoming["similar_documents"] or "[]")
            if (
                incoming["status"] != "review"
                or incoming["trashed_at"]
                or existing["trashed_at"]
                or not existing["is_active"]
            ):
                raise ValueError("版本状态已变化，请刷新后重试")
            if existing_id not in {m["id"] for m in choices}:
                raise ValueError("所选文献不在相似版本候选中")
            remaining = (
                [] if action == "keep_existing" else [m for m in choices if m["id"] != existing_id]
            )
            db.execute(
                "UPDATE documents SET status=?,similar_documents=? WHERE id=?",
                (
                    "review" if remaining else "ready",
                    json.dumps(remaining, ensure_ascii=False),
                    doc_id,
                ),
            )
            if action == "keep_existing":
                db.execute(
                    "UPDATE documents SET is_active=0,superseded_by=? WHERE id=?",
                    (existing_id, doc_id),
                )
            elif action == "replace_existing":
                db.execute(
                    "UPDATE documents SET is_active=0,superseded_by=? WHERE id=?",
                    (doc_id, existing_id),
                )
            db.execute(
                "INSERT INTO version_decisions(incoming_id,existing_id,action) VALUES(?,?,?)",
                (doc_id, existing_id, action),
            )
        return self.document(doc_id)

    def rows(self, scope):
        with self.connect() as db:
            sql = "SELECT c.*, d.title FROM chunks c JOIN documents d ON c.document_id=d.id WHERE d.status='ready' AND d.trashed_at IS NULL AND d.is_active=1"
            args = []
            if scope:
                sql += " AND document_id IN (" + ",".join("?" for _ in scope) + ")"
                args = scope
            return [dict(r) for r in db.execute(sql, args)]


def retrieve(rows, question, query_vector=None, limit=6, anchor_question=None, coverage_ids=None):
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
    ranked = sorted(ranks, key=ranks.get, reverse=True)
    # 长书中章节号、图号被分词成常见整数后，正确图注可能被大量相似段落挤出。
    # 只对用户明确写出的图／表／公式编号补召回；数值本身不触发规则。
    anchors = reference_anchors(question if anchor_question is None else anchor_question)
    anchored = []
    for kind, number in anchors:
        pattern = anchor_pattern(kind, number)
        heading = anchor_pattern(kind, number, heading=True)
        matches = [i for i, row in enumerate(rows) if pattern.search(row["text"])]
        matches.sort(
            key=lambda i: (
                not bool(heading.search(rows[i]["text"])),
                -ranks.get(i, 0),
                i,
            )
        )
        # 先覆盖各个明确编号，再补混合检索结果；不扩大总体证据预算。
        if matches:
            anchored.append(matches[0])
    # 硬件问题优先检查明确的训练配置标题；不把结果/变体表中的 GPU
    # 或通用模型名当作训练配置，不引入任何论文专属答案。
    original = question if anchor_question is None else anchor_question
    hardware = []
    if re.search(r"\bgpus?\b|显卡|硬件", original, re.I):
        heading = re.compile(
            r"^\s*(?:\d+(?:\.\d+)*\s+)?(?:hardware(?:\s+and\s+schedule)?|"
            r"training infrastructure|硬件配置|训练硬件)\s*$",
            re.I | re.M,
        )
        hardware = [i for i in ranked if heading.search(rows[i]["text"])]
        # 标题段落即使未进入混合检索前四十名，也可在当前文献范围补召回。
        hardware += [
            i for i, row in enumerate(rows) if i not in hardware and heading.search(row["text"])
        ]
    architecture = []
    if re.search(r"架构|编码器|解码器|\bencoder\b|\bdecoder\b|\barchitecture\b", original, re.I):
        heading = re.compile(
            r"^\s*(?:\d+(?:\.\d+)*\s+)?(?:Model Architecture\b|Encoder and Decoder Stacks\b|模型架构|编码器与解码器)",
            re.I | re.M,
        )
        architecture = [i for i in ranked if heading.search(rows[i]["text"])]
        architecture += [
            i for i, row in enumerate(rows) if i not in architecture and heading.search(row["text"])
        ]
    overview = []
    if re.search(
        r"研究问题|研究目标|研究目的|主要贡献|核心贡献|创新点|主要方法|核心方法|"
        r"主要研究|主要内容|概括|总结|概述|解决.{0,8}问题|"
        r"main research|research (?:question|problem|objective)|main contribution|summari[sz]e|overview",
        original,
        re.I,
    ):
        # 概括题本身不含论文主题词，语义近邻可能只有书目或相关工作。
        # 按当前范围逐篇补召回摘要及后续段，不使用题名或论文专属答案。
        groups = []
        for doc_id in dict.fromkeys(row.get("document_id") for row in rows):
            candidates = [
                i for i, row in enumerate(rows) if row.get("document_id") == doc_id
            ]
            candidates.sort(key=lambda i: (rows[i].get("page", 0), rows[i].get("offset", 0)))
            abstract = next(
                (position for position, i in enumerate(candidates)
                 if rows[i].get("page", 0) <= 3
                 and re.search(r"\babstract\b|摘\s*要", rows[i]["text"], re.I)),
                None,
            )
            start = abstract if abstract is not None else 0
            groups.append(candidates[start : start + 3])
        for position in range(3):
            overview.extend(group[position] for group in groups if position < len(group))
    sections = []
    section_queries = [
        (r"局限|不足|缺点|未来工作|改进方向|\blimitations?\b|future work",
         r"limitations?|conclusions?|discussion|future work|局限性?|不足|结论|讨论"),
        (r"主要方法|核心方法|具体方法|方法流程|如何结合|\bmethodology\b|main method",
         r"methodology|(?:our |proposed )?(?:methods?|approach)|方法|方法论"),
        (r"实验设置|实验配置|数据集|评估指标|experimental setup|datasets?|evaluation metrics?",
         r"experimental (?:setup|settings?)|experiments?|datasets?|evaluation|实验|数据集|评估"),
    ]
    for intent, titles in section_queries:
        if not re.search(intent, original, re.I):
            continue
        heading = re.compile(
            rf"^\s*(?:(?:\d+(?:\.\d+)*|[A-Z])\.?\s+)?(?:{titles})\s*$",
            re.I | re.M,
        )
        matches = [i for i, row in enumerate(rows) if heading.search(row["text"])]
        matches.sort(key=lambda i: (not bool(re.search(r"limitations?|局限", rows[i]["text"], re.I)), -ranks.get(i, 0)))
        for i in matches:
            following = [
                j for j, row in enumerate(rows)
                if row.get("document_id") == rows[i].get("document_id")
                and (row.get("page", 0), row.get("offset", 0))
                >= (rows[i].get("page", 0), rows[i].get("offset", 0))
            ]
            following.sort(key=lambda j: (rows[j].get("page", 0), rows[j].get("offset", 0)))
            sections.extend(following[:2])
    order = list(dict.fromkeys(anchored + hardware[:2] + architecture + sections + overview + ranked))
    if coverage_ids:
        groups = []
        topic = comparison_topic(original)
        for doc_id in dict.fromkeys(coverage_ids):
            candidates = [i for i in order if rows[i].get("document_id") == doc_id]
            extras = [
                i
                for i, row in enumerate(rows)
                if row.get("document_id") == doc_id and i not in candidates
            ]
            extras.sort(key=lambda i: (ranks.get(i, 0), scores[i]), reverse=True)
            candidates += extras
            if topic:
                # 仅原始问题触发维度补召回，不能由模型扩写的词改变问题目标。
                candidates.sort(
                    key=lambda i: comparison_priority(rows[i]["text"], topic), reverse=True
                )
            groups.append(candidates)
        # 逐轮取每篇的下一段，防止长篇或重复模型名占满剩余预算。
        order = []
        for position in range(limit):
            for group in groups:
                if position < len(group):
                    order.append(group[position])
            if len(order) >= limit:
                break
    order = order[:limit]
    selected = [dict(rows[i], score=ranks.get(i, 0)) for i in order]
    return (
        expand_comparison_evidence(selected, rows, comparison_topic(original))
        if coverage_ids
        else selected
    )


ANCHOR_KINDS = {
    "figure": r"(?:figure|fig\.?|图)",
    "table": r"(?:table|表)",
    "equation": r"(?:equation|eq\.?|公式|式)",
}


def reference_anchors(question):
    found = []
    for kind, label in ANCHOR_KINDS.items():
        for match in re.finditer(
            rf"(?<![a-zA-Z]){label}\s*[（(]?\s*(\d+(?:\.\d+)+)(?![\d.])", question, re.I
        ):
            found.append((kind, match.group(1)))
    return list(dict.fromkeys(found))[:6]


def anchor_pattern(kind, number, heading=False):
    label = ANCHOR_KINDS[kind]
    prefix = r"^\s*" if heading else r"(?<![a-zA-Z])"
    return re.compile(rf"{prefix}{label}\s*[（(]?\s*{re.escape(number)}(?![\d.])", re.I | re.M)


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
