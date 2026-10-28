"""在保存原文上定位数字制及英文作者年份制引用，使用字符索引和物理页码。"""

import hashlib
import re

from .graph import excerpt, references

ENTRY = re.compile(r"(?m)^[ \t]*\[(\d{1,4})\][ \t]+(?=\S)")
MENTION = re.compile(r"\[(\d{1,4}(?:\s*(?:[,，;；]|[-–−])\s*\d{1,4})*)\]")
YEAR = re.compile(r"\b(?:19|20)\d{2}[a-z]?\.\s")


def unwrapped(text):
    """移除 PDF 行末断词符，保留可回到原字符串的字符索引。"""
    skip = {i for m in re.finditer(r"-\s*\n\s*", text) for i in range(m.start(), m.end())}
    mapping = [i for i in range(len(text)) if i not in skip]
    return "".join(text[i] for i in mapping), mapping


def author_entries(text):
    """保守识别 Name, Name and Name. 2018a. 书目，不把标题行当作者。"""
    found = []
    particles = {"and", "et", "al", "de", "del", "van", "von", "der", "di", "da", "dos"}
    for year in YEAR.finditer(text):
        starts = [
            m.end() for m in re.finditer(r"\n", text[max(0, year.start() - 700) : year.start()])
        ]
        base = max(0, year.start() - 700)
        starts = ([0] if base == 0 else []) + [base + s for s in starts]
        for start in starts:
            header = text[start : year.start()].strip()
            if not header.endswith(".") or len(header) > 700:
                continue
            if re.search(r"\d", header) or re.search(r"(?<![A-Z])\.", header[:-1]):
                continue
            if any(len(word) > 1 for word in re.findall(r"\b(\w+)\.", header[:-1])):
                continue
            previous = text[:start].rstrip().split("\n")[-1] if start else ""
            if previous.endswith((",", " and", "-")):
                continue
            words = re.findall(r"[^\W\d_]+(?:['’\-][^\W\d_]+)*", header)
            if len(words) < 2 or any(not w[0].isupper() and w not in particles for w in words):
                continue
            if re.search(r"[^\w\s.,’'\-À-ž]", header):
                continue
            # 第一位作者的姓来自第一个逗号／and 之前，要求给出名字和姓。
            first = re.split(r",|\s+and\s+", header)[0].strip(" .")
            parts = first.split()
            if len(parts) < 2:
                continue
            surname = parts[-1]
            found.append(
                {
                    "start": start,
                    "year": year[0].strip().rstrip("."),
                    "surname": surname,
                    "authors": header,
                    "year_end": year.end(),
                    "label": f"{surname} et al., {year[0].strip().rstrip('.')}"
                    if "," in header
                    else f"{surname}"
                    + (
                        " and " + header.rsplit(" and ", 1)[1].strip(" .").split()[-1]
                        if " and " in header
                        else ""
                    )
                    + f", {year[0].strip().rstrip('.')}",
                }
            )
            break
    return found


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
    style = "numeric" if matches else "author-year"
    clean, mapping = unwrapped(section["text"])
    authors = author_entries(clean) if not matches else []
    candidates = (
        [{"start": m.start(), "number": int(m[1]), "label": f"[{m[1]}]"} for m in matches]
        if matches
        else [{**a, "start": mapping[a["start"]], "number": i + 1} for i, a in enumerate(authors)]
    )
    entries, ambiguous = {}, set()
    for i, found in enumerate(candidates):
        number = found["number"]
        end = candidates[i + 1]["start"] if i + 1 < len(candidates) else len(section["text"])
        # 超长书目通常意味着格式无法分割；宁可不提供跳转。
        if end - found["start"] > 8000:
            continue
        if style == "author-year":
            own_end = mapping[found["year_end"] - 1] + 1
            if YEAR.search(section["text"], own_end, end):
                # 中间存在无法分割的另一条书目时，拒绝把多篇混成一个来源。
                continue
        evidence = []
        for span in section["spans"]:
            start = max(found["start"], span["global_start"])
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
        entries[number] = {"number": number, "label": found["label"], "evidence": evidence}
    entries = {n: entry for n, entry in entries.items() if n not in ambiguous}
    mentions = []
    if section["spans"]:
        first = section["spans"][0]
        for page in pages:
            if page["page"] > first["page"]["page"]:
                break
            text = page["text"]
            stop = first["start"] if page["page"] == first["page"]["page"] else len(text)
            for found in MENTION.finditer(text[:stop]) if style == "numeric" else []:
                targets = numbers(found[1])
                # 不给未知编号或混合含糊的数字组制造链接。
                if targets and all(n in entries for n in targets):
                    mentions.append(
                        {"numbers": targets, **excerpt(doc_id, page, found.start(), found.end())}
                    )
            if style == "author-year":
                body, positions = unwrapped(text[:stop])
                for candidate in candidates:
                    same = [
                        c
                        for c in candidates
                        if c["surname"].casefold() == candidate["surname"].casefold()
                        and c["year"] == candidate["year"]
                    ]
                    if len(same) != 1 or candidate["number"] not in entries:
                        continue
                    pattern = re.compile(
                        r"(?<!\w)"
                        + re.escape(candidate["surname"])
                        + r"(?:\s+et\s+al\.?|\s+(?:and|&)\s+(?P<second>[A-Z][\w’'-]+))?\s*,?\s*\(?\s*"
                        + re.escape(candidate["year"])
                        + r"(?![a-z\d])\)?"
                    )
                    for match in pattern.finditer(body):
                        second = match.group("second")
                        if second and not re.search(
                            r"\b" + re.escape(second) + r"\b", candidate["authors"]
                        ):
                            continue
                        mentions.append(
                            {
                                "numbers": [candidate["number"]],
                                **excerpt(
                                    doc_id,
                                    page,
                                    positions[match.start()],
                                    positions[match.end() - 1] + 1,
                                ),
                            }
                        )
    mentions.sort(key=lambda item: (item["page"], item["start"]))
    return {
        "document_id": doc_id,
        "fingerprint": hashlib.sha256(
            (
                "reading-links-v2\n"
                + "\n".join(f"{p['page']}:{p['method']}:{p['text']}" for p in pages)
            ).encode()
        ).hexdigest(),
        "offset_encoding": "unicode-code-points",
        "style": style,
        "entries": list(entries.values()),
        "mentions": mentions,
        "limitations": "支持独立行 [数字] 书目，以及可识别的英文作者列表＋年份书目。作者年份同姓同年冲突不自动链接；脚注、上标、特殊姓名及 OCR 错字可能无法跳转，仍需核对原页。",
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
