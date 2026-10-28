"""对明确架构谓词做保守对比；不根据模型名称补全知识。"""

import re


def architecture_comparison(question, evidence):
    if not re.search(r"架构|architecture", question, re.I) or not re.search(
        r"encoder|decoder|编码器|解码器", question, re.I
    ):
        return None
    profiles = {}
    for item in evidence:
        text = re.sub(r"(\w)-\s*\n\s*(\w)", r"\1\2", item["text"])
        text = re.sub(r"\s+", " ", text)
        pair = re.search(
            r"\b(?:The\s+)?(?P<model>[A-Za-z][\w-]+)\s+follows\s+this\s+overall\s+architecture\b.{0,350}?\bfor\s+both\s+the\s+encoder\s+and\s+decoder\b",
            text,
            re.I,
        )
        single = re.search(
            r"\b(?P<model>[A-Za-z][\w-]+)[’']s\s+model\s+architecture\s+is\s+(?P<description>[^.!?]{1,140}?)\bencoder\b",
            text,
            re.I,
        )
        found = profiles.setdefault(item["document_id"], {})
        if pair and not re.search(r"\bnot\b|\bwithout\b", pair.group(), re.I):
            found.setdefault((pair["model"], "encoder_decoder"), (item, False))
        if single and not re.search(r"\bnot\b|\bwithout\b", single["description"], re.I):
            found.setdefault(
                (single["model"], "encoder"),
                (item, "bidirectional" in single["description"].lower()),
            )
    if len(profiles) != 2 or any(len(p) != 1 for p in profiles.values()):
        return None
    entries = [next(iter(p.items())) for p in profiles.values()]
    pair = next((e for e in entries if e[0][1] == "encoder_decoder"), None)
    single = next((e for e in entries if e[0][1] == "encoder"), None)
    if not pair or not single or pair[0][0].lower() == single[0][0].lower():
        return None
    pair_name, single_name = pair[0][0], single[0][0]
    direction = "双向 " if single[1][1] else ""
    answer = (
        f"{pair_name} 的架构同时包含编码器和解码器。[E1]\n\n"
        f"{single_name} 的原文架构描述为多层 {direction}Transformer 编码器。[E2]\n\n"
        f"据这两篇架构描述对比，{single_name} 采用编码器结构，未采用 {pair_name} 的解码器部分。"
        "这里是对架构构成的归纳，不表示原文描述了实际删除代码的过程。[E1][E2]"
    )
    # “多层／Transformer”也必须由该句明确支持，不能从名字推断。
    single_text = re.sub(r"(\w)-\s*\n\s*(\w)", r"\1\2", single[1][0]["text"])
    if not re.search(r"multi-layer\s+(?:bidirectional\s+)?Transformer", single_text, re.I):
        return None
    return {
        "answer": answer,
        "sources": [dict(pair[1][0], label="E1"), dict(single[1][0], label="E2")],
    }
