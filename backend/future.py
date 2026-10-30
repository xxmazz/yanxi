"""对照已入库原文与检索时较新 arXiv 摘要，不据此宣称 idea 从未被尝试。"""

from datetime import datetime, timezone


def prepare(graph, search):
    origins = []
    for node in graph["nodes"]:
        for source in node["future_work"][:1]:
            origins.append({"title": node["title"], "year": node["year"], **source})
    origins.sort(key=lambda source: source.get("year") or 0, reverse=True)
    origins = origins[:4]
    years = [source["year"] for source in origins if source.get("year")]
    if not origins:
        raise ValueError("所选文献未找到明确的后续研究原文，请先选择有 Future Work 的论文")
    if search.get("stale"):
        raise ValueError("arXiv 返回过期缓存，不能作为本轮更新研究核对，请稍后重试")
    after_year = max(years) if years else None
    candidates = []
    seen = set()
    for item in search.get("items", []):
        year = int(item["published"][:4])
        if after_year is not None and year <= after_year:
            continue
        if item["base_id"] in seen or len(item.get("abstract", "").strip()) < 30:
            continue
        seen.add(item["base_id"])
        candidates.append(
            {
                "label": f"E{len(candidates) + 1}",
                "title": item["title"],
                "arxiv_id": item["arxiv_id"],
                "published": item["published"],
                "url": item["url"],
                "text": item["abstract"][:1900],
                "page": 0,
                "topic_similarity": item.get("topic_similarity"),
            }
        )
        if len(candidates) == 4:
            break
    return {
        "fingerprint": graph["fingerprint"],
        "checked_at": datetime.now(timezone.utc).isoformat(),
        "search_query": search.get("search_query", ""),
        "fetched_at": search.get("fetched_at"),
        "cached": search.get("cached", False),
        "after_year": after_year,
        "origins": origins,
        "candidates": candidates,
        "scope": "检索至多8篇最新候选，有本地E5时按主题重排，最多概括4篇较新摘要。相似度不是相关概率；不是全网查新，摘要不能证明完整实验覆盖；无匹配也不代表方向尚未被尝试。",
    }
