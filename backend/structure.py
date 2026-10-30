"""按页提取表格候选和编号公式；坐标、原图及空单元格随结果保留。"""

import csv
import io
import json
import re
import subprocess
import sys
from pathlib import Path

VERSION = 2
MATH = re.compile(r"[=∑∏∫√∂∞≈≤≥]|softmax|sin\(|cos\(|log\(", re.I)


def box(values, width, height):
    return [round(v / scale, 6) for v, scale in zip(values, [width, height, width, height])]


def equations(page, words):
    items = []
    for label in words:
        if not re.fullmatch(r"\(\d+(?:[a-z]|\.\d+)?\)", label["text"], re.I):
            continue
        # 右侧独立编号加数学符号；参考文献及正文括号数字不能单独成为公式。
        if label["x0"] < page.width * 0.65:
            continue
        center = (label["top"] + label["bottom"]) / 2
        nearby = [
            w
            for w in words
            if abs((w["top"] + w["bottom"]) / 2 - center) < 18 and w["x1"] < label["x0"] - 4
        ]
        if not nearby or not MATH.search(" ".join(w["text"] for w in nearby)):
            continue
        # 只保留编号周围的公式行，文字转写不冒充 LaTeX；完整表达式以原图核对。
        x0 = max(0, min(w["x0"] for w in nearby) - 8)
        top = max(0, min(w["top"] for w in nearby + [label]) - 8)
        bottom = min(page.height, max(w["bottom"] for w in nearby + [label]) + 8)
        bbox = (x0, top, min(page.width, label["x1"] + 5), bottom)
        items.append(
            {
                "kind": "equation",
                "label": label["text"],
                "bbox": box(bbox, page.width, page.height),
                "text": page.crop(bbox).extract_text(x_tolerance=2) or "",
                "method": "numbered-math-line",
                "warning": "文本层转写可能丢失上下标、分数和符号；原图保留原始排版。",
            }
        )
    return items[:30]


def tables(page, words):
    captions = []
    for word in words:
        if re.match(r"^(?:Table\s*\d|表\s*\d)", word["text"], re.I):
            captions.append(word)
        elif word["text"].lower() in ("table", "表"):
            suffix = next(
                (
                    w
                    for w in words
                    if abs(w["top"] - word["top"]) < 2
                    and 0 <= w["x0"] - word["x1"] < 15
                    and re.match(r"^\d+[:.]?$", w["text"])
                ),
                None,
            )
            if suffix:
                captions.append({**word, "text": word["text"] + " " + suffix["text"]})
    # 表题和同宽水平线共同限定区域，避免把网络图/注意力热图的网格当作数据表。
    rules = sorted(
        [e for e in page.edges if e["orientation"] == "h" and e["width"] > page.width * 0.18],
        key=lambda e: e["top"],
    )
    groups = []
    for rule in rules:
        group = next(
            (
                g
                for g in groups
                if abs(g[0]["x0"] - rule["x0"]) < 4
                and abs(g[0]["x1"] - rule["x1"]) < 4
                and rule["top"] - g[-1]["top"] < page.height * 0.3
            ),
            None,
        )
        if group is None:
            groups.append([rule])
        else:
            group.append(rule)
    result = []
    for group in groups:
        if len(group) < 2:
            continue
        first, last = group[0], group[-1]
        nearby = [
            c
            for c in captions
            if first["top"] - 90 < c["top"] < last["top"] + 60
            and first["x0"] - 40 < c["x0"] < first["x1"]
        ]
        if not nearby or last["top"] - first["top"] < 24:
            continue
        caption = min(nearby, key=lambda c: abs(c["top"] - first["top"]))
        bbox = (
            max(0, first["x0"] - 1),
            max(0, first["top"] - 1),
            min(page.width, first["x1"] + 1),
            min(page.height, last["top"] + 1),
        )
        crop = page.crop(bbox)
        candidates = crop.find_tables(
            {"vertical_strategy": "text", "horizontal_strategy": "text", "min_words_vertical": 3}
        )
        for table in candidates:
            rows = geometric_rows(crop.extract_words(return_chars=True), table.columns)
            if not 3 <= len(rows) <= 100 or not 2 <= len(rows[0]) <= 20:
                continue
            # 合并单元格保留 null，空单元格保留空串；不填补缺失数值。
            titlebox = (
                max(0, caption["x0"] - 2),
                max(0, caption["top"] - 1),
                min(page.width, first["x1"] + 20),
                min(page.height, caption["bottom"] + 12),
            )
            result.append(
                {
                    "kind": "table",
                    "label": caption["text"],
                    "caption": page.crop(titlebox).extract_text() or caption["text"],
                    "bbox": box(bbox, page.width, page.height),
                    "rows": rows,
                    "text": crop.extract_text() or "",
                    "method": "caption-rules-text-grid",
                    "warning": "自动分列候选：多层表头、跨列及科学计数法可能分错；导出前请对照原图核验。",
                }
            )
            break
    return result[:15]


def geometric_rows(words, columns):
    """使用检测到的列界和完整单词，跨列文字不切碎，后续格保留 null。"""
    lines = []
    for word in sorted(words, key=lambda w: (w["top"], w["x0"])):
        center = (word["top"] + word["bottom"]) / 2
        line = next((line for line in reversed(lines) if abs(line[0] - center) < 3), None)
        if line is None:
            lines.append([center, [word]])
        else:
            line[1].append(word)
    rows = []
    for _, line in lines:
        cells = [""] * len(columns)
        for word in sorted(line, key=lambda w: w["x0"]):
            overlaps = [
                i
                for i, col in enumerate(columns)
                if min(col.bbox[2], word["x1"]) - max(col.bbox[0], word["x0"]) > 2
            ]
            if not overlaps:
                continue
            start = overlaps[0]
            if cells[start] is None:
                while start > 0 and cells[start] is None:
                    start -= 1
            cells[start] = ((cells[start] or "") + " " + scientific_word(word)).strip()
            for i in overlaps[1:]:
                if not cells[i]:
                    cells[i] = None
        if any(cells):
            rows.append(cells)
    return rows


def scientific_word(word):
    """仅在真实字形字号和高度都支持时，以 ^ 保留科学计数法的指数。"""
    text = word["text"]
    chars = word.get("chars", [])
    if not chars or not re.fullmatch(r"[+-]?\d+(?:\.\d+)?[·×x]10\d+", text):
        return text
    size = max(c["size"] for c in chars)
    normal = [c for c in chars if c["size"] >= size * 0.85]
    baseline = max(c["bottom"] for c in normal)
    raised = [c["size"] < size * 0.85 and c["bottom"] < baseline - 1 for c in chars]
    first = next((i for i, value in enumerate(raised) if value), len(chars))
    if (
        first < len(chars)
        and all(raised[first:])
        and all(c["text"].isdigit() for c in chars[first:])
    ):
        prefix = "".join(c["text"] for c in chars[:first])
        if prefix.endswith("10"):
            return prefix + "^" + "".join(c["text"] for c in chars[first:])
    return text


def extract(path, number):
    import pdfplumber

    with pdfplumber.open(path, pages=[number]) as pdf:
        if not pdf.pages:
            raise ValueError("页码不存在")
        page = pdf.pages[0]
        if len(page.chars) > 50000 or len(page.edges) > 15000:
            raise ValueError("本页结构过于复杂，请使用原页查看")
        words = page.extract_words(x_tolerance=2)
        items = tables(page, words) + equations(page, words)
        for i, item in enumerate(items):
            item["id"] = str(i)
        return {
            "version": VERSION,
            "page": number,
            "width": page.width,
            "height": page.height,
            "items": items,
            "note": "按需分析当前文本页。扫描页需先核对 OCR；未检测到不代表原文没有表格或公式。",
        }


def analyze(store, doc_id, number):
    doc = store.document(doc_id)
    if not doc or not 1 <= number <= doc["pages"]:
        raise KeyError("文献或页码不存在")
    if doc["status"] not in ("ready", "review"):
        raise ValueError("请等待文献解析完成")
    page = store.page(doc_id, number)
    if page and page["method"] == "ocr":
        return {
            "version": VERSION,
            "page": number,
            "items": [],
            "note": "扫描页暂不自动重建表格或数学公式，请结合本地 OCR 和原页阅读。",
        }
    directory = store.root / f"structure-v{VERSION}" / doc["id"]
    directory.mkdir(parents=True, exist_ok=True)
    cached = directory / f"{number}.json"
    if cached.exists():
        return json.loads(cached.read_text())
    try:
        result = subprocess.run(
            [
                sys.executable,
                "-m",
                "backend.structure",
                str(store.root / (doc["id"] + ".pdf")),
                str(number),
            ],
            capture_output=True,
            timeout=45,
            check=True,
        )
        if len(result.stdout) > 4_000_000:
            raise ValueError("结构化结果过大，请查看原页")
        payload = json.loads(result.stdout)
    except (OSError, subprocess.SubprocessError, json.JSONDecodeError) as exc:
        raise ValueError("本页结构提取失败或超时，请使用原页；可稍后重试") from exc
    # 相同页并发使用独立临时文件，读者只会看到完整结果。
    import tempfile

    with tempfile.NamedTemporaryFile(
        mode="w", dir=directory, delete=False, encoding="utf-8"
    ) as temp:
        json.dump(payload, temp, ensure_ascii=False)
    Path(temp.name).replace(cached)
    return payload


def table_csv(item):
    output = io.StringIO(newline="")
    writer = csv.writer(output)
    for row in item["rows"]:
        # CSV 在电子表格中打开时不得执行 PDF 来源的单元格公式。
        writer.writerow(
            [
                (
                    "'" + cell
                    if cell.lstrip().startswith(("=", "+", "-", "@"))
                    and not re.fullmatch(r"[+-]?\d+(?:\.\d+)?", cell.strip())
                    else cell
                )
                if cell is not None
                else ""
                for cell in row
            ]
        )
    return "\ufeff" + output.getvalue()


if __name__ == "__main__":
    print(json.dumps(extract(Path(sys.argv[1]), int(sys.argv[2])), ensure_ascii=False))
