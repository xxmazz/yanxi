"""保守读取原生 PDF 线性署名区；无法核对时不猜作者顺序。"""

import re


def author_ordinal(question):
    matches = list(re.finditer(r"第\s*([0-9一二三四五六七八九十两]+)\s*[位名]?\s*作者", question))
    if not matches:
        return None
    if len(matches) > 1:
        return -1  # 多序号请求交给明确的范围不足提示，不只回答其中一个。
    match = matches[0]
    tail = question[match.end() :].strip("？?。，. \t")
    if tail and not re.search(r"谁|姓名|名字|哪位|机构|单位|公司|工作|署名", tail):
        return None
    value = match[1]
    if value.isascii() and value.isdigit():
        return int(value)
    digits = {c: i for i, c in enumerate("零一二三四五六七八九")}
    digits["两"] = 2
    if value in digits:
        return digits[value]
    if value.count("十") == 1:
        left, right = value.split("十")
        if (not left or left in digits) and (not right or right in digits):
            return digits.get(left, 1) * 10 + digits.get(right, 0)
    return None


def ordered_author(document, page, ordinal):
    if not page or page["method"] != "native":
        return None
    names = [name.strip() for name in document.get("authors", "").split(";") if name.strip()]
    if not 2 <= len(names) <= 100 or len(set(names)) != len(names):
        return None
    text = page["text"]
    abstract = re.search(r"(?im)^\s*(?:abstract|摘要)\s*$", text)
    if abstract is None:
        return None
    front = text[: abstract.start()]
    positions = []
    for name in names:
        pattern = re.escape(name).replace(r"\ ", r"[ \t]+")
        matches = list(re.finditer(rf"(?m)^[ \t]*{pattern}([ \t∗*†‡\d,]*)$", front))
        if len(matches) != 1:
            return None
        positions.append(matches[0])
    if any(a.start() >= b.start() for a, b in zip(positions, positions[1:])):
        return None
    # 若解析器把一排人名和一排机构分别输出，就不能把所有机构归给
    # 最后一个人；这里只接受每个人名之间确有署名信息的线性布局。
    if any(
        not re.search(r"[^\W\d_]", front[a.end() : b.start()])
        for a, b in zip(positions, positions[1:])
    ):
        return None
    if not 1 <= ordinal <= len(names):
        return {"out_of_range": True, "count": len(names)}
    index = ordinal - 1
    match = positions[index]
    end = positions[index + 1].start() if index + 1 < len(names) else len(front)
    block = front[match.start() : end].strip()
    lines = front[match.end() : end].strip().splitlines()
    affiliations = []
    for line in lines:
        line = line.strip()
        if "@" in line or not line:
            continue
        if re.search(
            r"university|institute|college|research|laborator|department|school|\bbrain\b|大学|学院|研究|实验室|公司",
            line,
            re.I,
        ):
            affiliations.append(line)
    notes = []
    for symbol in ("†", "‡"):
        if symbol in match.group(1):
            note = re.search(rf"(?m)^{symbol}[^\n]+", text)
            if note:
                notes.append(note.group())
    return {"name": names[index], "affiliations": affiliations, "quote": block, "notes": notes}
