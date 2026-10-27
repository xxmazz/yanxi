"""正文词组重合与本地语义向量联合筛选；文件名不参与判断。"""

import hashlib
import heapq
import re
from difflib import SequenceMatcher


def identity(text):
    match = re.search(r"arxiv\s*:\s*(\d{4}\.\d{4,5})(?:v(\d+))?", text, re.I)
    return (match.group(1), int(match.group(2) or 0)) if match else ("", 0)


def content_profile(text):
    text = re.sub(r"(\w)-\s*\n\s*(\w)", r"\1\2", text.lower())
    words = re.findall(r"[a-z0-9]+|[\u4e00-\u9fff]", text)
    hashes = {
        int.from_bytes(
            hashlib.blake2b(" ".join(words[i : i + 5]).encode(), digest_size=8).digest(), "big"
        )
        for i in range(max(0, len(words) - 4))
    }
    return {"sketch": heapq.nsmallest(4096, hashes), "count": len(hashes), "words": len(words)}


def content_overlap(a, b):
    left, right = set(a.get("sketch", [])), set(b.get("sketch", []))
    sample = set(heapq.nsmallest(4096, left | right))
    if not sample:
        return 0.0
    jaccard = len(left & right & sample) / len(sample)
    minimum = min(a["count"], b["count"])
    return (
        min(1.0, jaccard * (a["count"] + b["count"]) / ((1 + jaccard) * minimum))
        if minimum
        else 0.0
    )


def mean_vector(chunks):
    import numpy as np

    vectors = [np.frombuffer(c["vector"], dtype=np.float32) for c in chunks if c.get("vector")]
    if not vectors:
        return None
    vector = np.mean(vectors, axis=0)
    norm = np.linalg.norm(vector)
    return (vector / norm).astype(np.float32).tobytes() if norm else None


def compare_documents(incoming, existing):
    a, b = incoming["profile"], existing["profile"]
    if min(a.get("words", 0), b.get("words", 0)) < 80:
        return None
    overlap = content_overlap(a, b)
    length_ratio = min(a["words"], b["words"]) / max(a["words"], b["words"])
    semantic = None
    if incoming.get("vector") and existing.get("vector"):
        import numpy as np

        av = np.frombuffer(incoming["vector"], dtype=np.float32)
        bv = np.frombuffer(existing["vector"], dtype=np.float32)
        if av.shape == bv.shape:
            semantic = float(np.clip(np.dot(av, bv), -1, 1))
    same_id = bool(
        incoming.get("arxiv_id") and incoming.get("arxiv_id") == existing.get("arxiv_id")
    )
    title_similarity = SequenceMatcher(
        None, (incoming.get("title") or "").lower(), (existing.get("title") or "").lower()
    ).ratio()
    strong_overlap = overlap >= 0.72 and length_ratio >= 0.45
    semantic_match = (
        overlap >= 0.35
        and (semantic or 0) >= 0.95
        and title_similarity >= 0.65
        and length_ratio >= 0.45
    )
    version_match = same_id and overlap >= 0.45 and length_ratio >= 0.25
    if not (strong_overlap or semantic_match or version_match):
        return None
    new_version, old_version = incoming.get("version", 0), existing.get("version", 0)
    recommendation = "review"
    reason = "正文词组高度重合，请核对是否为同一论文或不同排版。"
    if same_id and new_version and old_version and new_version != old_version:
        recommendation = "keep_existing" if old_version > new_version else "replace_existing"
        reason = (
            f"正文高度重合；原文 arXiv 标识显示当前库为 v{old_version}，新文件为 v{new_version}。"
        )
    return {
        "id": existing["id"],
        "title": existing["title"],
        "overlap": round(overlap, 4),
        "semantic_similarity": round(semantic, 4) if semantic is not None else None,
        "version": old_version,
        "arxiv_id": existing.get("arxiv_id", ""),
        "recommendation": recommendation,
        "reason": reason,
    }
