"""按 PDF 单词坐标读取明确的双语 BLEU 列；无法确认布局时不计算。"""

import re
import subprocess
import xml.etree.ElementTree as ET
from decimal import Decimal


def compact(text):
    return re.sub(r"[\W_]", "", text).lower()


def bleu_column(question):
    if not re.search(r"(?<![a-z])BLEU(?![a-z])", question, re.I):
        return None
    de = bool(
        re.search(r"EN[-–]DE|English[-\s]+to[-\s]+German|英[语文]?[-到至]?德", question, re.I)
    )
    fr = bool(
        re.search(r"EN[-–]FR|English[-\s]+to[-\s]+French|英[语文]?[-到至]?法", question, re.I)
    )
    return "EN-DE" if de and not fr else "EN-FR" if fr and not de else None


def table_from_words(words):
    metrics = [
        w
        for w in words
        if w["text"] == "BLEU"
        and all(
            any(h["text"] == label and 0 < h["y0"] - w["y0"] < 30 for h in words)
            for label in ("EN-DE", "EN-FR")
        )
    ]
    if len(metrics) != 1:
        return None
    metric = metrics[0]
    center = (metric["x0"] + metric["x1"]) / 2
    headers = {}
    for label in ("EN-DE", "EN-FR"):
        candidates = [w for w in words if w["text"] == label and 0 < w["y0"] - metric["y0"] < 30]
        if not candidates:
            return None
        headers[label] = min(candidates, key=lambda w: abs((w["x0"] + w["x1"]) / 2 - center))
    left, right = [(headers[c]["x0"] + headers[c]["x1"]) / 2 for c in ("EN-DE", "EN-FR")]
    if not 15 < right - left < 100 or abs((left + right) / 2 - center) > 15:
        return None
    if abs(headers["EN-DE"]["y0"] - headers["EN-FR"]["y0"]) > 2:
        return None
    half = (right - left) / 2
    start = max(w["y1"] for w in headers.values())
    rows, last_y = [], start
    remaining = sorted((w for w in words if w["y0"] > start), key=lambda w: (w["y0"], w["x0"]))
    while remaining:
        y = remaining[0]["y0"]
        line = [w for w in remaining if abs(w["y0"] - y) < 2]
        remaining = [w for w in remaining if abs(w["y0"] - y) >= 2]
        # 连续单行表格；跨行名称、跨页表格及多层合并单元格不猜测。
        if y - last_y > 20:
            break
        labels = sorted((w for w in line if w["x1"] < left - half), key=lambda w: w["x0"])
        name = re.sub(r"\s*\[\d+(?:,\s*\d+)*\]", "", " ".join(w["text"] for w in labels)).strip()
        values = {}
        for column, x in (("EN-DE", left), ("EN-FR", right)):
            cells = [w for w in line if x - half <= (w["x0"] + w["x1"]) / 2 < x + half]
            if len(cells) > 1 or any(not re.fullmatch(r"\d+(?:\.\d+)?", w["text"]) for w in cells):
                return None
            values[column] = cells[0]["text"] if cells else None
            if values[column] is not None and not 0 <= Decimal(values[column]) <= 100:
                return None
        if not name or not any(values.values()):
            return None
        rows.append({"model": name, "values": values})
        last_y = y
    if len(rows) < 3 or len({r["model"] for r in rows}) != len(rows):
        return None
    return {"metric": "BLEU", "columns": ["EN-DE", "EN-FR"], "rows": rows}


def read_bleu_table(path, number, raw):
    try:
        result = subprocess.run(
            ["pdftotext", "-f", str(number), "-l", str(number), "-bbox", str(path), "-"],
            check=True,
            capture_output=True,
            timeout=20,
        )
        if len(result.stdout) > 5_000_000:
            return None
        xml = ET.fromstring(result.stdout)
        words = [
            {
                "text": e.text or "",
                "x0": float(e.attrib["xMin"]),
                "x1": float(e.attrib["xMax"]),
                "y0": float(e.attrib["yMin"]),
                "y1": float(e.attrib["yMax"]),
            }
            for e in xml.iter()
            if e.tag.rsplit("}", 1)[-1] == "word"
        ]
        table = table_from_words(words)
        caption = re.search(r"(?im)^Table\s+\d+[:.]", raw)
        header = re.search(r"(?m)^Model\s*$", raw)
        if not table or not caption or not header or header.start() <= caption.start():
            return None
        # 几何识别结果还须能在保存的原文逐行定位，不能制造新原文。
        matched_lines = []
        for row in table["rows"]:
            label = re.escape(row["model"]).replace(r"\ ", r"\s+")
            pattern = rf"^\s*{label}(?:\s*\[\d+(?:,\s*\d+)*\])?\s+\d"
            candidates = [line for line in raw.splitlines() if re.search(pattern, line)]
            if len(candidates) != 1 or any(
                v not in candidates[0] for v in row["values"].values() if v
            ):
                return None
            matched_lines.append(candidates[0])
        final = matched_lines[-1]
        end = raw.index(final, header.end()) + len(final)
        return dict(
            table,
            caption=raw[caption.start() : header.start()].strip(),
            quote=raw[caption.start() : end],
        )
    except (OSError, subprocess.SubprocessError, ET.ParseError, ValueError, KeyError):
        return None


def table_answer(question, table):
    column = bleu_column(question)
    if column is None:
        return None
    targets = [r for r in table["rows"] if compact(r["model"]) in compact(question)]
    targets = [
        r
        for r in targets
        if not any(
            r is not other and compact(r["model"]) in compact(other["model"]) for other in targets
        )
    ]
    if len(targets) != 1:
        return None
    target = targets[0]
    score = target["values"][column]
    if score is None:
        return {
            "answer": f"该表中 {target['model']} 的 {column} BLEU 单元格为空，不能从其他语言列补值。[E1]",
            "calculation": None,
            "insufficient_evidence": True,
        }
    answer = f"按原文表格，{target['model']} 在 {column} 的 BLEU 为 {score}。[E1]"
    calculation = None
    if re.search(
        r"提升|差|比较|对比|state.of.the.art|\bSOTA\b|improv|increase|difference", question, re.I
    ):
        family = target["model"].split()[0]
        if not re.search(r"previous|state.of.the.art", table["caption"], re.I) or not re.search(
            rf"\b{re.escape(family)}\b", table["caption"], re.I
        ):
            return None
        baselines = [
            r
            for r in table["rows"]
            if r["model"].split()[0] != family and r["values"][column] is not None
        ]
        if not baselines:
            return None
        best = max(Decimal(r["values"][column]) for r in baselines)
        winners = [r for r in baselines if Decimal(r["values"][column]) == best]
        delta = Decimal(score) - best
        names = "、".join(r["model"] for r in winners)
        answer += (
            f"\n\n该表列出的其他模型系列（包含集成模型）中，最高为 {names}，BLEU 为 {best}。[E1]"
        )
        answer += f"\n\n据这两项表格数值计算：{score} − {best} = {delta} BLEU 点。此差值为程序计算，并非原文直接报告。[E1]"
        calculation = {
            "operation": "subtract",
            "left": score,
            "right": str(best),
            "result": str(delta),
            "column": column,
            "metric": "BLEU",
        }
    return {"answer": answer, "calculation": calculation}
