"""对实际服务器执行翻译、缓存与原文一致性检查；不以自动检查替代翻译质量评审。"""

import argparse
import hashlib
import json
import time

import httpx

parser = argparse.ArgumentParser()
parser.add_argument("--base-url", default="http://127.0.0.1:18474")
parser.add_argument("--document-id", required=True)
parser.add_argument("--page", type=int, default=1)
parser.add_argument("--whole", action="store_true")
args = parser.parse_args()
client = httpx.Client(base_url=args.base_url, trust_env=False, timeout=30)


def request(method, path, **kwargs):
    response = client.request(method, path, **kwargs)
    response.raise_for_status()
    return response.json()


def wait(job):
    deadline = time.monotonic() + 2400
    while job["status"] in ("queued", "running"):
        if time.monotonic() > deadline:
            raise TimeoutError("翻译超时，任务仍可在页面查看或取消")
        time.sleep(1)
        job = request("GET", "/api/translation/jobs/" + job["id"])
    assert job["status"] == "ready", job
    return job


base = f"/api/documents/{args.document_id}"
original = request("GET", base + f"/pages/{args.page}")
start = time.monotonic()
job = wait(
    request(
        "POST",
        base + "/translation",
        json={"target": "zh", **({} if args.whole else {"page": args.page})},
    )
)
seconds = round(time.monotonic() - start, 2)
page = request("GET", base + f"/translation/pages/{args.page}")
assert page["complete"]
assert "".join(item["source"] for item in page["segments"]) == original["text"]
assert page["source_hash"] == hashlib.sha256(original["text"].encode()).hexdigest()
verified_pages = []
for number in job["pages"]:
    cached_page = request("GET", base + f"/translation/pages/{number}")
    raw_page = request("GET", base + f"/pages/{number}")
    assert cached_page["complete"]
    assert "".join(item["source"] for item in cached_page["segments"]) == raw_page["text"]
    assert cached_page["source_hash"] == hashlib.sha256(raw_page["text"].encode()).hexdigest()
    verified_pages.append(number)
start = time.monotonic()
repeat = wait(request("POST", base + "/translation", json={"target": "zh", "page": args.page}))
repeat_seconds = round(time.monotonic() - start, 2)
assert request("GET", base + f"/translation/pages/{args.page}")["segments"] == page["segments"]
sample = "We train the Transformer on 8 GPUs for 3.5 days with a learning rate of 0.0003. The objective is $L = -\\log p(y|x)$. Keep the code `model.eval()` unchanged."
protected = wait(request("POST", "/api/translation/text", json={"target": "zh", "text": sample}))
for token in ["8", "3.5", "0.0003", "$L = -\\log p(y|x)$", "`model.eval()`"]:
    assert token in protected["result"], protected
chinese = "本文提出一种检索增强生成方法，将本地知识库与语言模型结合。实验在 8 张 GPU 上运行，学习率为 0.0003。"
english = wait(request("POST", "/api/translation/text", json={"target": "en", "text": chinese}))
assert "0.0003" in english["result"] and "8" in english["result"]
assert any(char.isascii() and char.isalpha() for char in english["result"])
exported = None
if args.whole:
    response = client.get(base + "/translation/export")
    response.raise_for_status()
    exported = {
        "bytes": len(response.content),
        "sha256": hashlib.sha256(response.content).hexdigest(),
    }
print(
    json.dumps(
        {
            "document_id": args.document_id,
            "page": args.page,
            "whole": args.whole,
            "model": request("GET", "/api/health")["model"],
            "seconds": seconds,
            "timing_note": "任务可能复用已完成段落，此时间不代表整篇首次翻译耗时",
            "all_requested_pages_verified": verified_pages,
            "segments": len(page["segments"]),
            "completed": job["completed"],
            "total": job["total"],
            "original_text_exactly_preserved": True,
            "source_hash_verified": True,
            "cache_reused": True,
            "cached_page_seconds": repeat_seconds,
            "sample_translation": page["segments"][min(1, len(page["segments"]) - 1)][
                "translation"
            ],
            "protected_source": sample,
            "protected_translation": protected["result"],
            "protected_items_preserved": True,
            "chinese_source": chinese,
            "academic_english": english["result"],
            "export": exported,
        },
        ensure_ascii=False,
        indent=2,
    )
)
