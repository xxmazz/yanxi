"""由文献库 References 原文构建有向引用图；相似度不作为引用边。"""

import hashlib
import json
import re
import unicodedata
from datetime import datetime, timezone

import networkx as nx

VERSION = "references-v2"
MAX_DOCUMENTS = 30
HEADING = re.compile(r"(?im)^\s*(?:\d+[.\s]+)?(?:references|bibliography|参考文献)\s*[:：]?\s*$")
APPENDIX = re.compile(
    r"(?im)^\s*(?:appendix|appendices|supplementary material|附录)\b[^\n]*$|"
    r"^\s*[A-Z](?:\.\d+)?\s+(?:Prompts?|Additional|Supplementary|Appendix|Implementation|Experimental|Proofs?|Hyperparameters?)\b[^\n]*$"
)


def sha(text):
    return hashlib.sha256(text.encode()).hexdigest()


def normalized(text, mapped=False):
    """忽略大小写、空白、标点和印刷连字，保留每字符原文坐标。"""
    chars, locations = [], []
    for index, char in enumerate(text):
        for value in unicodedata.normalize("NFKD", char).casefold():
            if value.isalnum():
                chars.append(value)
                locations.append(index)
    result = "".join(chars)
    return (result, locations) if mapped else result


def references(pages):
    pieces, spans, active, count = [], [], False, 0
    for page in pages:
        text, start = page["text"], 0
        if not active:
            heading = HEADING.search(text)
            if not heading:
                continue
            active, start = True, heading.end()
        stop = APPENDIX.search(text, start)
        end = stop.start() if stop else len(text)
        if end > start:
            if pieces:
                pieces.append("\n")
                count += 1
            part = text[start:end]
            spans.append(
                {
                    "global_start": count,
                    "global_end": count + len(part),
                    "page": page,
                    "start": start,
                }
            )
            pieces.append(part)
            count += len(part)
        if stop:
            break
    return {"text": "".join(pieces), "spans": spans}


def excerpt(doc_id, page, start, end):
    return {
        "document_id": doc_id,
        "page": page["page"],
        "start": start,
        "end": end,
        "quote": page["text"][start:end],
        "page_sha256": sha(page["text"]),
        "method": page["method"],
        "url": f"/api/documents/{doc_id}/pdf#page={page['page']}",
        "preview_url": f"/api/documents/{doc_id}/render/{page['page']}",
    }


def match_reference(section, target):
    text = section["text"]
    for identity in target["arxiv_ids"]:
        found = re.search(r"(?<![\w.])" + re.escape(identity) + r"(?:v\d+)?(?!\w|\.\d)", text, re.I)
        if found:
            return found.start(), found.end(), "arxiv_id"
    for doi in target["dois"]:
        found = re.search(r"(?<![\w])" + re.escape(doi) + r"(?![\w/])", text, re.I)
        if found:
            return found.start(), found.end(), "doi"
    if "normalized" not in section:
        section["normalized"] = normalized(text, mapped=True)
    clean, mapping = section["normalized"]
    for title in target["titles"]:
        title = normalized(title)
        # 短标题易与普通正文重合；只接受足够长的完整标题，拒绝模糊相似匹配。
        if len(title) < 18:
            continue
        for found in re.finditer(re.escape(title), clean):
            start, end = mapping[found.start()], mapping[found.end() - 1] + 1
            if (
                (start == 0 or not text[start - 1].isalnum())
                and (end == len(text) or not text[end].isalnum())
                and re.match(r"[^\S\n]*(?:[^\w\s]|\n|$)", text[end:])
            ):
                return start, end, "full_title"
    return None


def match_evidence(doc_id, section, start, end):
    lower, upper = max(0, start - 240), min(len(section["text"]), end + 300)
    result = []
    for span in section["spans"]:
        a, b = max(lower, span["global_start"]), min(upper, span["global_end"])
        if a < b:
            result.append(
                excerpt(
                    doc_id,
                    span["page"],
                    span["start"] + a - span["global_start"],
                    span["start"] + b - span["global_start"],
                )
            )
    return result


def first_author(authors):
    return normalized(re.split(r"[;,；]", authors or "")[0])


def groups(documents, pages, sources):
    parent = {d["id"]: d["id"] for d in documents}

    def root(value):
        while parent[value] != value:
            parent[value] = parent[parent[value]]
            value = parent[value]
        return value

    def identities(doc):
        return {
            re.sub(r"v\d+$", "", value)
            for value in [doc["arxiv_id"], *(s["arxiv_id"] for s in sources.get(doc["id"], []))]
            if value
        }

    for index, a in enumerate(documents):
        for b in documents[index + 1 :]:
            same_id = identities(a) & identities(b)
            same_title = (
                normalized(a["title"]) == normalized(b["title"])
                and len(normalized(a["title"])) >= 18
            )
            author_a, author_b = first_author(a["authors"]), first_author(b["authors"])
            if same_id or (
                same_title
                and author_a
                and author_a == author_b
                and (not identities(a) or not identities(b))
            ):
                component_a = {
                    identity
                    for member in documents
                    if root(member["id"]) == root(a["id"])
                    for identity in identities(member)
                }
                component_b = {
                    identity
                    for member in documents
                    if root(member["id"]) == root(b["id"])
                    for identity in identities(member)
                }
                if component_a and component_b and not component_a & component_b:
                    continue
                parent[root(b["id"])] = root(a["id"])
    result = {}
    for doc in documents:
        result.setdefault(root(doc["id"]), []).append(doc)
    nodes = []
    for members in result.values():
        # 同一作品只用一个版本计算出边，优先较新、较少 OCR 的原文。
        chosen = max(members, key=lambda d: (d["version"], -d["ocr_pages"], d["id"]))
        doc_id = chosen["id"]
        identities_all = sorted({i for doc in members for i in identities(doc)})
        official = [s for doc in members for s in sources.get(doc["id"], [])]
        identity = (
            "arxiv:" + identities_all[0]
            if identities_all
            else "document:" + min(d["id"] for d in members)
        )
        year = None
        if official and official[0].get("published"):
            year = int(official[0]["published"][:4])
        elif identities_all and re.match(r"\d{4}\.", identities_all[0]):
            year = 2000 + int(identities_all[0][:2])
        node = {
            "id": identity,
            "document_id": doc_id,
            "title": chosen["title"],
            "authors": chosen["authors"],
            "year": year,
            "documents": [
                {k: d[k] for k in ("id", "title", "version", "ocr_pages", "pages")} for d in members
            ],
            "arxiv_ids": identities_all,
            "titles": sorted({d["title"] for d in members}),
            "dois": sorted({s["doi"].strip().lower() for s in official if s.get("doi")}),
            "abstract_evidence": None,
            "future_work": [],
        }
        raw_pages = pages[doc_id]
        if raw_pages:
            first = raw_pages[0]
            abstract = re.search(
                r"(?im)^[ \t]*(?:abstract|摘要)(?:[ \t]*[:：.—–-][ \t]*|[ \t]*\n)",
                first["text"],
            )
            if abstract:
                start = abstract.end()
                ending = re.search(
                    r"(?im)^\s*(?:1[.\s]+)?introduction\b|^\s*关键词", first["text"][start:]
                )
                end = start + ending.start() if ending else len(first["text"])
                node["abstract_evidence"] = excerpt(doc_id, first, start, min(end, start + 1400))
            for page in raw_pages:
                ref_header = HEADING.search(page["text"])
                body = page["text"][: ref_header.start()] if ref_header else page["text"]
                intervals = []
                for found in re.finditer(
                    r"\bfuture (?:work|research|directions?)\b|未来(?:工作|研究|方向)", body, re.I
                ):
                    start, end = max(0, found.start() - 160), min(len(body), found.end() + 700)
                    if intervals and start <= intervals[-1][1]:
                        intervals[-1][1] = end
                    else:
                        intervals.append([start, end])
                for start, end in intervals:
                    if len(node["future_work"]) < 3:
                        node["future_work"].append(excerpt(doc_id, page, start, end))
                if ref_header:
                    break
        nodes.append(node)
    return sorted(nodes, key=lambda n: n["id"])


def load_library(store, document_ids):
    with store.connect() as db:
        db.execute("BEGIN")
        documents = [
            dict(row)
            for row in db.execute(
                "SELECT * FROM documents WHERE status='ready' AND trashed_at IS NULL AND is_active=1 ORDER BY id"
            )
        ]
        if document_ids:
            missing = set(document_ids) - {d["id"] for d in documents}
            if missing:
                raise ValueError("所选文献不存在、正在解析、待版本确认或已归档，请刷新后重选")
            documents = [d for d in documents if d["id"] in document_ids]
        if len(documents) > MAX_DOCUMENTS:
            raise ValueError(f"每次最多分析 {MAX_DOCUMENTS} 个 PDF，请选择需要构图的文献")
        pages, sources, size = {}, {}, 0
        for doc in documents:
            doc_id = doc["id"]
            pages[doc_id] = []
            for row in db.execute(
                "SELECT page,text,method FROM pages WHERE document_id=? ORDER BY page", (doc_id,)
            ):
                page = dict(row)
                size += len(page["text"])
                if size > 20_000_000:
                    raise ValueError("当前范围正文超过 2000 万字符，请缩小文献范围")
                pages[doc_id].append(page)
            if len(pages[doc_id]) != doc["pages"]:
                raise ValueError("文献页数据不完整，请等待解析完成或重新解析")
            table = db.execute(
                "SELECT name FROM sqlite_master WHERE name='document_sources'"
            ).fetchone()
            if table:
                sources[doc_id] = [
                    json.loads(r["metadata"])
                    for r in db.execute(
                        "SELECT metadata FROM document_sources WHERE document_id=?", (doc_id,)
                    )
                ]
    return documents, pages, sources


def build_graph(store, document_ids=()):
    documents, pages, sources = load_library(store, document_ids)
    nodes = groups(documents, pages, sources)
    edges, warnings = [], []
    for node in nodes:
        section = references(pages[node["document_id"]])
        node["reference_pages"] = [span["page"]["page"] for span in section["spans"]]
        if not section["text"].strip():
            warnings.append(f"《{node['title']}》未识别到 References 标题，本轮不推断其出边")
            continue
        for target in nodes:
            if node["id"] == target["id"]:
                continue
            matched = match_reference(section, target)
            if matched:
                start, end, method = matched
                edges.append(
                    {
                        "source": node["id"],
                        "target": target["id"],
                        "match_method": method,
                        "matched_text": section["text"][start:end],
                        "evidence": match_evidence(node["document_id"], section, start, end),
                    }
                )
    graph = nx.DiGraph()
    graph.add_nodes_from(n["id"] for n in nodes)
    graph.add_edges_from((e["source"], e["target"]) for e in edges)
    ranks = nx.pagerank(graph, alpha=0.85, tol=1e-10, max_iter=300) if nodes else {}
    positions = (
        nx.spring_layout(graph, seed=42, iterations=80, scale=1)
        if len(nodes) > 1
        else {n["id"]: [0, 0] for n in nodes}
    )
    max_in = max((graph.in_degree(n["id"]) for n in nodes), default=0)
    for node in nodes:
        incoming, outgoing = graph.in_degree(node["id"]), graph.out_degree(node["id"])
        node.update(
            {
                "in_degree": incoming,
                "out_degree": outgoing,
                "pagerank": round(ranks[node["id"]], 8),
                "role": "局部基石候选"
                if incoming and incoming == max_in
                else "局部较少被引"
                if edges
                else "关系不足",
                "x": round(float(positions[node["id"]][0]) * 250 + 400, 2),
                "y": round(float(positions[node["id"]][1]) * 210 + 290, 2),
            }
        )
    fingerprint = sha(
        json.dumps(
            {
                "version": VERSION,
                "documents": [
                    {
                        "id": d["id"],
                        "title": d["title"],
                        "authors": d["authors"],
                        "arxiv_id": d["arxiv_id"],
                        "version": d["version"],
                        "ocr_pages": d["ocr_pages"],
                        "pages": [(p["page"], p["method"], sha(p["text"])) for p in pages[d["id"]]],
                        "sources": sources.get(d["id"], []),
                    }
                    for d in documents
                ],
            },
            ensure_ascii=False,
            sort_keys=True,
        )
    )
    return {
        "fingerprint": fingerprint,
        "version": VERSION,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "document_ids": [d["id"] for d in documents],
        "nodes": nodes,
        "edges": edges,
        "stats": {
            "documents": len(documents),
            "works": len(nodes),
            "edges": len(edges),
            "components": nx.number_weakly_connected_components(graph) if nodes else 0,
            "isolated": len(list(nx.isolates(graph))),
        },
        "warnings": warnings,
        "interpretation": "箭头从引用论文指向被引论文；指标只统计当前已入库范围，不是全球引用量或创新性评价。",
    }
