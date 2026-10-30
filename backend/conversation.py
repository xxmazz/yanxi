"""仅延续上次有效回答的文献范围；历史答案不作为检索证据。"""

import re

FOLLOWUP = re.compile(
    r"^\s*(?:其中|这篇|这份|该论文|该文|它|上述|前面|刚才|接上文|接着|继续|"
    r"那(?:么)?[，,：:]?\s*(?:它|这|其中)|"
    r"(?:in\s+)?(?:this|that)\s+(?:paper|study)|its\b|it\b|what\s+about\b)",
    re.I,
)


def context_scope(question, explicit_ids, previous_ids):
    """显式选择优先；普通新问题不继承；只把指代追问限定到此前引用原件。"""
    if explicit_ids or not FOLLOWUP.search(question) or not previous_ids:
        return explicit_ids, False
    return list(dict.fromkeys(previous_ids)), True
