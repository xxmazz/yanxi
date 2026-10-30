"""书目题名检索与保守身份核对；网络来源只使用 arXiv 官方元数据。"""

import json
import re
from datetime import datetime, timezone

from .discovery import DiscoveryError, query_expression
from .following import reference_candidates
from .graph import normalized

VERSION = 1


def fields(entry):
    quote = "\n".join(e["quote"] for e in entry["evidence"])
    text = re.sub(r"-\s*\n\s*", "", quote)
    text = re.sub(r"^\s*\[\d+\]\s*", "", text)
    text = " ".join(text.split())
    # 不在作者缩写 J. / M. 内切分；作者年份书目在署名后跳过年份。
    boundary = re.search(r"(?<=[a-zà-ž])\.\s+", text)
    authors, title = "", ""
    if boundary:
        authors = text[: boundary.start()]
        rest = text[boundary.end() :]
        rest = re.sub(r"^(?:19|20)\d{2}[a-z]?\.\s+", "", rest)
        ending = re.search(r"[.!?](?:\s|$)", rest)
        title = rest[: ending.start() + (1 if ending[0].startswith("?") else 0)] if ending else rest
    years = re.findall(r"(?<!\d)((?:19|20)\d{2})(?!\d)", text)
    # 太短/过长或作者区不像姓名时不制造题名；仍可展示原文。
    if not 8 <= len(title) <= 300 or not authors or len(authors) > 900:
        title = ""
    return {
        "reference": entry["label"],
        "title": title,
        "authors": authors,
        "year": int(years[-1]) if years else None,
        "quote": quote[:8000],
        "pages": sorted({e["page"] for e in entry["evidence"]}),
    }


def verify(reference, item):
    reasons = []
    if not reference["title"] or normalized(reference["title"]) != normalized(item["title"]):
        reasons.append("完整题名不一致")
    header_words = set(re.findall(r"[^\W\d_]+", reference["authors"], re.UNICODE))
    header_words = {normalized(word) for word in header_words}
    authors = item.get("authors", [])
    checked = authors[:1] if re.search(r"\bet\s+al\b", reference["authors"], re.I) else authors[:2]
    matched = []
    for author in checked:
        parts = re.findall(r"[^\W\d_]+", author, re.UNICODE)
        # 姓与给名首字母同时核对，避免同姓作者通过；允许书目给名缩写。
        if (
            len(parts) >= 2
            and normalized(parts[-1]) in header_words
            and any(
                word.startswith(normalized(parts[0])[0])
                for word in header_words
                if word != normalized(parts[-1])
            )
        ):
            matched.append(author)
    if not checked or len(matched) != len(checked):
        reasons.append("首位及次位作者姓氏未同时在署名区核对")
    year = reference["year"]
    published = item.get("published", "")
    try:
        posted = datetime.fromisoformat(published.replace("Z", "+00:00")).year
    except (ValueError, TypeError):
        posted = None
    if year is None or posted is None or not -3 <= posted - year <= 1:
        reasons.append("书目年份缺失或与预印本发表年份不相容")
    return {
        "verified": not reasons,
        "reasons": reasons
        or [
            "完整题名一致（忽略排版空白、大小写及标点）",
            "作者姓氏在书目署名区核对",
            "预印本年份在书目年份前3年至后1年范围内",
        ],
        "matched_authors": matched,
        "reference_year": year,
        "published_year": posted,
    }


class ReferenceResolver:
    def __init__(self, store):
        self.store = store
        with store.connect() as db:
            db.execute("""CREATE TABLE IF NOT EXISTS reference_resolutions(
                document_id TEXT, fingerprint TEXT, reference TEXT, payload TEXT,
                PRIMARY KEY(document_id,fingerprint,reference))""")

    def saved(self, doc_id, fingerprint):
        with self.store.connect() as db:
            return {
                row[0]: json.loads(row[1])
                for row in db.execute(
                    "SELECT reference,payload FROM reference_resolutions WHERE document_id=? AND fingerprint=?",
                    (doc_id, fingerprint),
                )
            }

    def candidates(self, doc_id, navigation):
        result = reference_candidates(navigation)
        saved = self.saved(doc_id, navigation["fingerprint"])
        explicit = set(result["identified_references"])
        seen = {re.sub(r"v\d+$", "", item["arxiv_id"]) for item in result["items"]}
        unresolved = []
        for entry in navigation["entries"]:
            if entry["label"] in explicit:
                continue
            reference = fields(entry)
            resolution = saved.get(entry["label"])
            if (
                resolution
                and resolution.get("version") == VERSION
                and resolution["status"] == "matched"
                and verify(reference, resolution["selected"])["verified"]
            ):
                selected = resolution["selected"]
                base = selected["base_id"]
                if base not in seen:
                    seen.add(base)
                    result["items"].append(
                        {
                            "arxiv_id": selected["arxiv_id"],
                            "reference": entry["label"],
                            "quote": reference["quote"],
                            "pages": reference["pages"],
                            "title": selected["title"],
                            "match_method": "title-authors-year",
                            "match": selected["match"],
                            "resolution_checked_at": resolution["checked_at"],
                            "metadata_url": selected["url"],
                        }
                    )
                continue
            unresolved.append({**reference, "resolution": resolution})
        result.update(
            unresolved=len(unresolved),
            unresolved_entries=unresolved,
            resolved_by_title=sum(
                item.get("match_method") == "title-authors-year" for item in result["items"]
            ),
        )
        return result

    def resolve(self, doc_id, navigation, label, discovery):
        entry = next((entry for entry in navigation["entries"] if entry["label"] == label), None)
        if entry is None:
            raise ValueError("书目编号不存在，请重新读取参考文献")
        reference = fields(entry)
        if not reference["title"]:
            raise ValueError("此书目无法可靠分离完整题名，请使用探索页手动检索并核对原文")
        # 每次只查询一条；官方共享间隔及超时沿用 discovery.request，不运行网页爬虫。
        expression = query_expression(reference["title"])["search_query"].replace("all:", "ti:")
        response = discovery.request(
            {
                "search_query": expression,
                "start": 0,
                "max_results": 8,
                "sortBy": "relevance",
                "sortOrder": "descending",
            }
        )
        if response.get("stale"):
            raise DiscoveryError("官方查询返回过期缓存，不能保存本轮匹配，请稍后重试")
        candidates = []
        works = {}
        for item in response["items"]:
            checked = verify(reference, item)
            candidate = {
                key: item[key]
                for key in ["arxiv_id", "base_id", "title", "authors", "published", "url"]
            }
            candidate["match"] = checked
            candidates.append(candidate)
            if checked["verified"]:
                works[item["base_id"]] = candidate
        status = "matched" if len(works) == 1 else "ambiguous" if len(works) > 1 else "no_match"
        result = {
            "version": VERSION,
            "reference": label,
            "status": status,
            "checked_at": datetime.now(timezone.utc).isoformat(),
            "search_title": reference["title"],
            "query": expression,
            "cached": response.get("cached", False),
            "fetched_at": response.get("fetched_at"),
            "candidates": candidates,
            "selected": next(iter(works.values())) if len(works) == 1 else None,
            "note": "仅核对官方arXiv候选；完整标题、作者姓氏及兼容年份共同核对。无唯一匹配时不加入下载；未收录的期刊/会议论文仍需手动获取。",
        }
        return result

    def save(self, doc_id, fingerprint, result):
        with self.store.connect() as db:
            # 每个来源只保留当前原文指纹；旧批次内的匹配证据仍保留。
            db.execute(
                "DELETE FROM reference_resolutions WHERE document_id=? AND fingerprint!=?",
                (doc_id, fingerprint),
            )
            db.execute(
                "INSERT OR REPLACE INTO reference_resolutions VALUES(?,?,?,?)",
                (doc_id, fingerprint, result["reference"], json.dumps(result, ensure_ascii=False)),
            )
