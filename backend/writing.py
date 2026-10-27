"""以入库原文为证据撰写研究计划；书目和引用编号由服务端建立。"""

import json
import re
from datetime import datetime, timezone
from urllib.parse import quote

from .core import validate_answer
from .graph import build_graph

VERSION = "writing-v1"


def plain(value, limit=400):
    if not isinstance(value, str) or not value.strip() or len(value) > limit:
        raise ValueError("写作字段为空、类型错误或超过长度限制")
    value = " ".join(value.split())
    if re.search(r"https?://|www\.|doi\s*:|arxiv\s*:|\[(?:\d+|E\d+|R\d+)\]", value, re.I):
        raise ValueError("模型在正文中添加了未核验的链接或引用标号")
    return value


def markdown(text):
    return re.sub(r"([\\`*_{}\[\]<>#!|])", r"\\\1", " ".join(text.split()))


def prepare(store, document_ids):
    if not document_ids:
        raise ValueError("请明确选择至少一篇已解析文献")
    graph = build_graph(store, document_ids)
    if len(graph["nodes"]) > 6:
        raise ValueError("一次最多引用六篇作品，请缩小选择范围；同作品版本会合并")
    evidence, references, skipped = [], [], []
    for node in graph["nodes"]:
        source = node["abstract_evidence"]
        if not source or len(source["quote"].strip()) < 15:
            skipped.append({"title": node["title"], "reason": "未识别到足够的第一页摘要原文"})
            continue
        official = []
        with store.connect() as db:
            if db.execute(
                "SELECT name FROM sqlite_master WHERE name='document_sources'"
            ).fetchone():
                for member in node["documents"]:
                    official.extend(
                        json.loads(row["metadata"])
                        for row in db.execute(
                            "SELECT metadata FROM document_sources WHERE document_id=? ORDER BY imported DESC",
                            (member["id"],),
                        )
                    )
        metadata = official[0] if official else {}
        authors = metadata.get("authors")
        authors = (
            "; ".join(authors)
            if isinstance(authors, list) and all(isinstance(a, str) for a in authors)
            else node["authors"]
        )
        number = len(evidence) + 1
        arxiv = next(
            (
                value
                for value in node["arxiv_ids"]
                if re.fullmatch(r"(?:\d{4}\.\d{4,5}|[a-zA-Z.-]+/\d{7})", value)
            ),
            None,
        )
        reference = {
            "number": number,
            "label": f"E{number}",
            "document_id": node["document_id"],
            "title": metadata.get("title") or node["title"],
            "authors": authors or "作者信息待核对",
            "year": node["year"],
            "metadata_origin": "arXiv 官方来源" if official else "PDF 解析信息",
            "arxiv_id": arxiv,
            "source_url": f"https://arxiv.org/abs/{quote(arxiv, safe='/')}" if arxiv else None,
            "pdf_url": f"/api/documents/{node['document_id']}/pdf",
            "evidence": source,
            "merged_versions": len(node["documents"]),
        }
        references.append(reference)
        evidence.append(
            {
                "label": reference["label"],
                "title": reference["title"],
                "document_id": source["document_id"],
                "page": source["page"],
                "text": source["quote"],
            }
        )
    if not evidence:
        raise ValueError("所选文献没有可核对的摘要原文，请补充文献或重新解析")
    return {
        "fingerprint": graph["fingerprint"],
        "document_ids": graph["document_ids"],
        "references": references,
        "evidence": evidence,
        "skipped": skipped,
    }


def list_field(raw, name, minimum, maximum):
    values = raw.get(name)
    if not isinstance(values, list) or not minimum <= len(values) <= maximum:
        raise ValueError(f"写作输出缺少有效的 {name} 列表")
    return [plain(value, 90 if name == "approach" else 260) for value in values]


def diagram_for(steps):
    nodes = (
        [{"id": "idea", "label": "研究问题"}]
        + [{"id": f"m{i}", "label": step} for i, step in enumerate(steps, 1)]
        + [{"id": "evaluation", "label": "实验与失败案例评估"}]
    )
    edges = [{"source": a["id"], "target": b["id"]} for a, b in zip(nodes, nodes[1:])]

    def mermaid_label(text):
        # 标签只作为字符串；原始换行、引号、HTML 或 Mermaid 语法不会被执行。
        return (
            text.replace("&", "&amp;")
            .replace("\\", "&#92;")
            .replace('"', "&quot;")
            .replace("<", "&lt;")
            .replace(">", "&gt;")
            .replace("[", "&#91;")
            .replace("]", "&#93;")
            .replace("`", "&#96;")
        )

    script = (
        "flowchart TD\n"
        + "\n".join(f'  {node["id"]}["{mermaid_label(node["label"])}"]' for node in nodes)
        + "\n"
        + "\n".join(f"  {edge['source']} --> {edge['target']}" for edge in edges)
        + "\n"
    )
    return {"nodes": nodes, "edges": edges, "mermaid": script, "kind": "拟议方法流程"}


def render_draft(idea, raw, bundle):
    if not isinstance(raw, dict):
        raise ValueError("模型没有返回结构化论文框架")
    title = plain(raw.get("title"), 120)
    problem = plain(raw.get("problem"), 260)
    approach = list_field(raw, "approach", 2, 5)
    evaluation = list_field(raw, "evaluation", 2, 4)
    claims = raw.get("claims")
    if not isinstance(claims, list):
        raise ValueError("缺少有来源的相关工作结论")
    for claim in claims:
        if isinstance(claim, dict):
            plain(claim.get("text"), 1000)
    _, labels, refused = validate_answer({"claims": claims}, bundle["evidence"])
    if refused or set(labels) != {e["label"] for e in bundle["evidence"]}:
        raise ValueError("相关工作未覆盖本轮全部摘要来源")
    # 逐条校验后的来源只能映射到本次服务端书目，拒绝模型自写书目信息。
    related = []
    for claim in claims:
        number = next(
            ref["number"] for ref in bundle["references"] if ref["label"] == claim["source"]
        )
        related.append(f"{markdown(claim['text'])} [{number}]")
    bibliography = []
    for ref in bundle["references"]:
        parts = [f"[{ref['number']}] {markdown(ref['authors'])}.", markdown(ref["title"]) + "."]
        if ref["year"]:
            parts.append(str(ref["year"]) + ".")
        if ref["source_url"]:
            parts.append(ref["source_url"])
        else:
            parts.append(f"文献库 PDF：{ref['document_id'][:12]}；书目信息来自 PDF 解析，需核对。")
        bibliography.append(" ".join(parts))
    proposal = [f"{i}. 拟开展：{markdown(step)}" for i, step in enumerate(approach, 1)]
    tests = [f"- 计划验证：{markdown(step)}" for step in evaluation]
    draft = (
        "\n\n".join(
            [
                "# " + markdown(title),
                "> 研究计划草稿：方法与评估为拟议内容，不代表已经完成实验；已有研究结论的编号对应下方真实文献。",
                "## Abstract\n\n本文拟围绕“"
                + markdown(problem)
                + "”开展研究。拟采用以下路径："
                + "；".join(markdown(s) for s in approach)
                + "。计划通过“"
                + "；".join(markdown(s) for s in evaluation)
                + "”检验方案，研究效果以实际实验为准。",
                "## 1. Introduction\n\n研究设想："
                + markdown(idea)
                + "。\n\n研究问题："
                + markdown(problem)
                + "。\n\n已有研究基础："
                + related[0],
                "## 2. Related Work\n\n" + "\n\n".join(related),
                "## 3. Proposed Method\n\n" + "\n".join(proposal),
                "## 4. Evaluation Plan\n\n" + "\n".join(tests),
                "## 5. Limitations\n\n相关工作仅依据本次选取的论文摘要，不能代替全文研究与语义核验。拟议方案的有效性、创新性与统计显著性需要真实实验及更新文献核验。",
                "## References\n\n" + "\n\n".join(bibliography),
            ]
        )
        + "\n"
    )
    return {
        "version": VERSION,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "idea": idea,
        "fingerprint": bundle["fingerprint"],
        "document_ids": bundle["document_ids"],
        "markdown": draft,
        "references": bundle["references"],
        "skipped": bundle["skipped"],
        "diagram": diagram_for(approach),
        "checks": [
            "文献范围与原文指纹",
            "逐条来源与原文摘录",
            "相关工作数值",
            "全部摘要覆盖",
            "服务端生成真实书目",
        ],
    }
