"""从保存正文中识别显式编号章节；不推测缺失标题或无编号的层级。"""

import re

from .graph import HEADING, excerpt

NUMBERED = re.compile(
    r"(?m)^[ \t]*(?P<path>[1-9]\d?(?:\.[1-9]\d?){0,2})\.?[ \t]+(?P<title>[^\n]{2,100})$"
)
SPECIAL = re.compile(r"(?im)^[ \t]*(?:abstract|摘要)[ \t]*$")


def outline(doc_id, pages):
    items, parents, seen = [], set(), set()
    last_chapter = 0
    for page in pages:
        text = page["text"]
        bibliography = HEADING.search(text)
        end = bibliography.start() if bibliography else len(text)
        matches = [(m.start(), m, False) for m in SPECIAL.finditer(text[:end])]
        matches += [(m.start(), m, True) for m in NUMBERED.finditer(text[:end])]
        for _, match, numbered in sorted(matches, key=lambda item: item[0]):
            path = match["path"] if numbered else ""
            title = match["title"].strip() if numbered else match[0].strip()
            level = path.count(".") + 1 if numbered else 1
            if numbered:
                if not (title[0].isupper() or "\u4e00" <= title[0] <= "\u9fff"):
                    continue
                if (
                    re.search(r"[=|\t]|[.!?。！？]\s*$", title)
                    or len(re.findall(r"(?<!\w)\d+(?:\.\d+)?", title)) > 1
                ):
                    continue
                if len(title.split()) > 14 or (
                    path.count(".") and path.rsplit(".", 1)[0] not in parents
                ):
                    continue
                chapter = int(path.split(".")[0])
                if chapter > 50 or chapter < last_chapter:
                    continue
                if level == 1:
                    last_chapter = chapter
                parents.add(path)
            key = path, title.casefold()
            if key in seen:
                continue
            seen.add(key)
            items.append(
                {
                    "path": path,
                    "title": title,
                    "level": level,
                    **excerpt(doc_id, page, match.start(), match.end()),
                }
            )
        if bibliography:
            label = re.search(
                r"(?:references|bibliography|参考文献)[ \t]*[:：]?[ \t]*$",
                bibliography[0].rstrip(),
                re.I,
            )
            start = bibliography.start() + label.start()
            items.append(
                {
                    "path": "",
                    "title": label[0].strip(),
                    "level": 1,
                    **excerpt(doc_id, page, start, bibliography.end()),
                }
            )
            break
    return items
