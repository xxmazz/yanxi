"""在保存原文上定位数字制引用。坐标使用 Python 字符索引和物理页码。"""

import hashlib
import re

from .graph import excerpt, references

ENTRY = re.compile(r"(?m)^[ \t]*\[(\d{1,4})\][ \t]+(?=\S)")
MENTION = re.compile(r"\[(\d{1,4}(?:\s*(?:[,，;；]|[-–−])\s*\d{1,4})*)\]")


def numbers(value):
    result = []
    for group in re.split(r"[,，;；]", value):
        bounds = re.split(r"[-–−]", group)
        low, high = int(bounds[0]), int(bounds[-1])
        if len(bounds) > 2 or high < low or high - low > 30:
            return []
        result.extend(range(low, high + 1))
    return sorted(set(result))


def build_links(doc_id, pages):
    section = references(pages)
    matches = list(ENTRY.finditer(section["text"]))
    entries, ambiguous = {}, set()
    for i, found in enumerate(matches):
        number = int(found[1])
        end = matches[i + 1].start() if i + 1 < len(matches) else len(section["text"])
        # 超长书目通常意味着格式无法分割；宁可不提供跳转。
        if end - found.start() > 8000:
            continue
        evidence = []
        for span in section["spans"]:
            start = max(found.start(), span["global_start"])
            stop = min(end, span["global_end"])
            if start < stop:
                evidence.append(
                    excerpt(
                        doc_id,
                        span["page"],
                        span["start"] + start - span["global_start"],
                        span["start"] + stop - span["global_start"],
                    )
                )
        if number in entries:
            ambiguous.add(number)
        entries[number] = {"number": number, "evidence": evidence}
    entries = {n: entry for n, entry in entries.items() if n not in ambiguous}
    mentions = []
    if section["spans"]:
        first = section["spans"][0]
        for page in pages:
            if page["page"] > first["page"]["page"]:
                break
            text = page["text"]
            stop = first["start"] if page["page"] == first["page"]["page"] else len(text)
            for found in MENTION.finditer(text[:stop]):
                targets = numbers(found[1])
                # 不给未知编号或混合含糊的数字组制造链接。
                if targets and all(n in entries for n in targets):
                    mentions.append(
                        {"numbers": targets, **excerpt(doc_id, page, found.start(), found.end())}
                    )
    return {
        "document_id": doc_id,
        "fingerprint": hashlib.sha256(
            "\n".join(f"{p['page']}:{p['method']}:{p['text']}" for p in pages).encode()
        ).hexdigest(),
        "offset_encoding": "unicode-code-points",
        "entries": list(entries.values()),
        "mentions": mentions,
        "limitations": "仅识别 References 中独立行起始的 [数字] 书目和正文方括号数字引用。作者年份制、脚注、上标与 OCR 错字可能无法跳转；数字匹配仍需核对原页。",
    }


def navigation(store, doc_id):
    # 同一数据库快照读取，避免重解析期间拼接不同版本页数据。
    with store.connect() as db:
        doc = db.execute("SELECT status,pages FROM documents WHERE id=?", (doc_id,)).fetchone()
        if doc is None:
            raise KeyError("文献不存在")
        if doc["status"] != "ready":
            raise ValueError("请等待文献解析与版本确认完成")
        pages = [
            dict(row)
            for row in db.execute(
                "SELECT page,text,method FROM pages WHERE document_id=? ORDER BY page", (doc_id,)
            )
        ]
        if len(pages) != doc["pages"] or sum(len(p["text"]) for p in pages) > 20_000_000:
            raise ValueError("页数据不完整或超过引用导航大小限制")
    return build_links(doc_id, pages)
