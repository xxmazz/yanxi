"""在明确选择的较新库内论文正文中检索研究方向证据。"""

from datetime import datetime, timezone

from .core import retrieve
from .graph import references, sha


def origins_from(graph):
    origins = [
        {"title": node["title"], "year": node["year"], **source}
        for node in graph["nodes"]
        for source in node["future_work"][:1]
    ]
    origins.sort(key=lambda item: item.get("year") or 0, reverse=True)
    if not origins:
        raise ValueError("原文范围没有明确Future Work片段，请重新选择")
    return origins[:4]


def evidence_for(store, graph, target_graph, topic, query_vector):
    origins = origins_from(graph)
    years = [item["year"] for item in origins if item.get("year")]
    if not years:
        raise ValueError("原文年份不明，请先补齐真实来源信息")
    after = max(years)
    source_ids = {document["id"] for node in graph["nodes"] for document in node["documents"]}
    groups = []
    for node in target_graph["nodes"]:
        if not node.get("year") or node["year"] <= after:
            raise ValueError("对照论文需有明确来源年份，并晚于原方向文献")
        if source_ids.intersection(document["id"] for document in node["documents"]):
            raise ValueError("原文和对照文献范围不能重叠")
        ids = [node["document_id"]]
        rows = store.rows(ids)
        doc = store.document(ids[0])
        pages = [store.page(ids[0], number) for number in range(1, doc["pages"] + 1)]
        ref_spans = references([page for page in pages if page])["spans"]
        # 排除书目区：别人论文的题名不是本篇正文贡献。
        body = []
        for row in rows:
            import re

            if re.search(
                r"(?im)^\s*(?:\d+[.\s]+)?(?:references|bibliography|参考文献)\s*[:：]?\s*$",
                row["text"],
            ):
                continue
            page = store.page(row["document_id"], row["page"])
            if not page or row["text"] not in page["text"]:
                continue
            low = page["text"].find(row["text"])
            abstract = node.get("abstract_evidence")
            if abstract and row["page"] == abstract["page"] and low < abstract["end"]:
                continue
            if any(
                span["page"]["page"] == row["page"]
                and low < (span["start"] + span["global_end"] - span["global_start"])
                and low + len(row["text"]) > span["start"]
                for span in ref_spans
            ):
                continue
            body.append(row)
        ranked = retrieve(body, topic, query_vector, limit=12)
        selected, seen_pages = [], set()
        for row in ranked:
            if row["page"] not in seen_pages:
                selected.append(row)
                seen_pages.add(row["page"])
            if len(selected) == 3:
                break
        selected += [row for row in ranked if row not in selected][: 3 - len(selected)]
        evidence = []
        for row in selected:
            page = store.page(row["document_id"], row["page"])
            start = page["text"].find(row["text"])
            evidence.append(
                {
                    "label": f"E{len(evidence) + 1}",
                    "document_id": row["document_id"],
                    "title": row["title"],
                    "page": row["page"],
                    "text": row["text"],
                    "start": start,
                    "end": start + len(row["text"]),
                    "page_sha256": sha(page["text"]),
                    "url": f"/api/documents/{row['document_id']}/pdf#page={row['page']}",
                }
            )
        groups.append(
            {
                "title": node["title"],
                "year": node["year"],
                "document_ids": ids,
                "evidence": evidence,
                "indexed_chunks": len(body),
            }
        )
    return {
        "checked_at": datetime.now(timezone.utc).isoformat(),
        "after_year": after,
        "origins": origins,
        "groups": groups,
        "origin_fingerprint": graph["fingerprint"],
        "target_fingerprint": target_graph["fingerprint"],
        "topic": topic,
        "scope": "在明确选择的较新已入库论文正文中，以BM25/E5检索每篇最多3段并由本地模型概括与拟议方向有关的工作，保留原页、原句及哈希。正文检索不等于逐字审查全篇，不据此断言某方向已完全解决或从未被尝试。",
    }
