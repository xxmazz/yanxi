"""从原始问题识别比较维度，仅辅助证据召回，不填入模型名称或答案。"""

import re
import unicodedata
from decimal import Decimal


def comparison_topic(question):
    if re.search(r"上下文|context\s*(?:window|size|length)|序列长度", question, re.I):
        return "context"
    if re.search(r"layer\s*norm|layernorm|层归一化|\bLN\b", question, re.I):
        return "normalization"
    if re.search(
        r"参数量|参数的变化|参数规模|参数数量|模型大小|parameter|model\s+size", question, re.I
    ):
        return "parameters"
    return None


def comparison_priority(text, topic):
    text = unicodedata.normalize("NFKC", text)
    text = re.sub(r"(\w)-\s*\n\s*(\w)", r"\1\2", text)
    text = re.sub(r"\s+", " ", text)
    patterns = {
        "context": [
            r"context\s+(?:window|size|length).{0,70}?\d",
            r"nctx\s*=\s*\d",
            r"sequences?\s+of\s+\d+\s+tokens",
            r"(?:上下文|序列)长度.{0,30}?\d",
        ],
        "parameters": [
            r"Parameters\s+Layers|nparams",
            r"\d+(?:\.\d+)?\s+(?:million|billion)\s+parameters",
            r"\d+(?:\.\d+)?\s*[MB]\s+parameter",
            r"model\s+specifications|model\s+and\s+architectures",
            r"smallest\s+model\s+is\s+equivalent|largest\s+model.{0,70}?parameters",
        ],
        "normalization": [
            r"layer\s*norm(?:alization)?.{0,80}?(?:moved|input|before|after|pre-)",
            r"additional\s+layer\s+norm|pre-normalization|post-normalization",
            r"layernorm|层归一化",
        ],
    }
    weights = [4, 2, 2, 2, 1] if topic == "parameters" else [1] * len(patterns.get(topic, []))
    return sum(
        weight
        for pattern, weight in zip(patterns.get(topic, []), weights)
        if re.search(pattern, text, re.I)
    )


def expand_comparison_evidence(selected, rows, topic):
    """指标段落在固定块边界被截断时，合并同页相邻重叠原文；不拼接跨页证据。"""
    if not topic:
        return selected
    expanded = []
    for item in selected:
        value = dict(item)
        if comparison_priority(item["text"], topic) > 0 and "offset" in item:
            neighbors = sorted(
                (
                    r
                    for r in rows
                    if r.get("document_id") == item.get("document_id")
                    and r.get("page") == item.get("page")
                    and r.get("offset", -1) > item["offset"]
                ),
                key=lambda r: r["offset"],
            )
            for neighbor in neighbors:
                end = value["offset"] + len(value["text"])
                estimate = end - neighbor["offset"]
                # 固定块入库时 strip 可能去掉边界空白；仍须逐字重叠匹配。
                matches = (
                    [
                        width
                        for width in range(
                            max(1, estimate - 8), min(len(neighbor["text"]), estimate + 8) + 1
                        )
                        if value["text"][-width:] == neighbor["text"][:width]
                    ]
                    if estimate > 0
                    else []
                )
                if not matches:
                    break
                overlap = max(matches)
                merged = value["text"] + neighbor["text"][overlap:]
                if len(merged) > 2000:
                    boundary = merged.rfind("\n", len(value["text"]), 2000)
                    value["text"] = merged[: boundary if boundary > 0 else 2000]
                    break
                value["text"] = merged
        expanded.append(value)
    return expanded


def explicit_configuration_facts(question, evidence):
    """读取明确配置谓词和简单参数表；不根据论文名补知识。"""
    topic = comparison_topic(question)
    for item in evidence:
        text = unicodedata.normalize("NFKC", item["text"])
        flat = re.sub(r"\s+", " ", text)
        facts = []
        if topic == "normalization" and re.search(
            r"Since layernorm\s*\[\s*\d+\]\s+is used extensively throughout the model", flat, re.I
        ):
            facts = [
                "本篇原文明确说明在模型中广泛使用 layernorm；这句话未说明其在子块输入或输出的具体位置。"
            ]
        if topic == "parameters":
            header = re.search(r"(?m)^Parameters\s+Layers\s+dmodel\s*$", text)
            if header:
                tail = text[header.end() :]
                values = []
                closed = False
                for line in tail.strip().splitlines():
                    row = re.fullmatch(r"\s*(\d+(?:\.\d+)?)([MB])\s+\d+\s+\d+\s*", line)
                    if not row:
                        closed = bool(re.match(r"Table\s+\d+[:.]", line))
                        break
                    values.append(
                        (Decimal(row[1]) * (10**6 if row[2] == "M" else 10**9), row[1] + row[2])
                    )
                if closed and len(values) >= 2 and len({v[0] for v in values}) == len(values):
                    low, high = min(values), max(values)
                    facts.append(
                        f"本篇参数表列出的模型规模为 {'、'.join(v[1] for v in values)}，最大为 {high[1]}。"
                    )
                    original = re.search(
                        r"smallest model is equivalent to the\s+original\s+([A-Za-z][\w-]*)",
                        flat,
                        re.I,
                    )
                    largest = re.search(
                        r"largest\s+model,\s+which\s+we\s+call\s+([A-Za-z][\w-]*)", flat, re.I
                    )
                    if original:
                        facts.append(
                            f"原文将最小模型等同于原始 {original[1]}；据该表与等价描述归纳，原始 {original[1]} 的对应规模为 {low[1]}。"
                            + (
                                f"原文将最大模型称为 {largest[1]}，表中规模为 {high[1]}。"
                                if largest
                                else ""
                            )
                        )
            named_header = re.search(
                r"(?m)^Model Name\s+nparams\s+nlayers\s+dmodel\s+nheads\s+dhead\s+Batch Size\s+Learning Rate\s*$",
                text,
            )
            if not facts and named_header:
                values = []
                closed = False
                for line in text[named_header.end() :].strip().splitlines():
                    row = re.fullmatch(
                        r"(.+?)\s+(\d+(?:\.\d+)?)([MB])\s+\d+\s+\d+\s+\d+\s+\d+\s+.+", line
                    )
                    if not row:
                        closed = bool(re.match(r"Table\s+\d+(?:\.\d+)*[:.]", line))
                        break
                    values.append(
                        (
                            Decimal(row[2]) * (10**6 if row[3] == "M" else 10**9),
                            row[1],
                            row[2] + row[3],
                        )
                    )
                if closed and len(values) >= 2 and len({v[1] for v in values}) == len(values):
                    low, high = min(values), max(values)
                    facts = [
                        f"本篇参数表的模型规模从 {low[2]} 到 {high[2]}；最大模型为 {high[1]}，总参数量为 {high[2]}。数值与 M／B 单位按原表保留。"
                    ]
            configuration = re.search(
                r"We trained a\s+(\d+)-layer decoder-only transformer", flat, re.I
            )
            total_reported = re.search(
                r"\d+(?:\.\d+)?\s*(?:[MB]|million|billion)\s+(?:total\s+)?parameters|parameter\s+count|total\s+parameters",
                flat,
                re.I,
            )
            if not facts and configuration and not total_reported:
                facts = [
                    f"本篇配置段说明使用 {configuration[1]} 层 decoder-only Transformer，但该段没有直接报告总参数量；不从层数猜测参数总数。"
                ]
        if facts:
            return {
                "claims": [{"text": fact, "source": item["label"]} for fact in facts],
                "insufficient_evidence": False,
            }
    return None
