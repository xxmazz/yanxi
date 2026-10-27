import asyncio
import logging
import os
import threading
from concurrent.futures import ThreadPoolExecutor
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, File, HTTPException, UploadFile
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field
from pypdf import PdfReader

from .core import Store, chunks_for_page, retrieve, validate_answer
from .models import LocalModels

log = logging.getLogger("yanxi")
store = Store(Path(os.getenv("YANXI_DATA_DIR", "runtime/library")))
models = LocalModels()
workers = ThreadPoolExecutor(max_workers=1, thread_name_prefix="pdf-index")
upload_lock = threading.Lock()
chat_lock = asyncio.Lock()
pending = threading.BoundedSemaphore(8)


def index_document(doc_id):
    try:
        reader = PdfReader(store.root / (doc_id + ".pdf"))
        if reader.is_encrypted:
            raise ValueError("加密 PDF 请先解密后再上传")
        if len(reader.pages) > 650:
            raise ValueError("当前支持不超过 650 页的 PDF")
        chunks, empty = [], 0
        for number, page in enumerate(reader.pages, 1):
            text = page.extract_text() or ""
            if len(text.strip()) < 25:
                empty += 1
            chunks.extend(chunks_for_page(text, number))
        if not chunks or empty > len(reader.pages) * 0.5:
            raise ValueError("此 PDF 主要为扫描图片，需要 OCR；当前文本解析未建立索引")
        vectors = models.encode([c["text"] for c in chunks])
        if vectors is not None:
            for chunk, vector in zip(chunks, vectors):
                chunk["vector"] = vector.tobytes()
        title = str((reader.metadata or {}).get("/Title", "")).strip()
        store.finish(doc_id, len(reader.pages), chunks, title[:300] or None)
    except Exception as exc:
        log.exception("PDF indexing failed: %s", doc_id)
        store.fail(doc_id, str(exc)[:300])
    finally:
        pending.release()


@asynccontextmanager
async def lifespan(app):
    if models.llm_path or models.embedding_path:
        await asyncio.to_thread(models.load)
    # 宕机前排队的任务重新提交，避免永久停在 queued 状态。
    for doc in store.documents():
        if doc["status"] == "queued":
            if pending.acquire(blocking=False):
                workers.submit(index_document, doc["id"])
            else:
                store.fail(doc["id"], "重启恢复队列已满，请重新导入")
    yield
    workers.shutdown(wait=True)


app = FastAPI(title="研析本地科研助手 API", lifespan=lifespan)


@app.get("/api/health")
def health():
    return {
        "status": "ok",
        "model_ready": models.llm is not None,
        "embedding_ready": models.encoder is not None,
        "model": Path(models.llm_path).name or None,
        "retrieval": "bm25+e5-rrf" if models.encoder is not None else "bm25",
        "ocr_ready": False,
    }


@app.get("/api/documents")
def documents():
    return store.documents()


@app.post("/api/documents", status_code=202)
def upload(file: UploadFile = File(...)):
    if not pending.acquire(blocking=False):
        raise HTTPException(429, "解析队列已满，请稍后重试")
    submitted = False
    try:
        data = file.file.read(100 * 1024 * 1024 + 1)
        if len(data) > 100 * 1024 * 1024:
            raise HTTPException(413, "PDF 不能超过 100 MB")
        if b"%PDF-" not in data[:1024]:
            raise HTTPException(400, "不是有效的 PDF 文件头")
        with upload_lock:
            doc, duplicate = store.create(data, Path(file.filename or "paper.pdf").name)
            # 已失败的文档允许原文件重试（例如后来启用了 OCR）。
            if duplicate and doc["status"] == "error":
                with store.connect() as db:
                    db.execute(
                        "UPDATE documents SET status='queued',error='' WHERE id=?",
                        (doc["id"],),
                    )
                doc = store.document(doc["id"])
                duplicate = False
            if not duplicate:
                workers.submit(index_document, doc["id"])
                submitted = True
        return {**doc, "duplicate": duplicate}
    finally:
        if not submitted:
            pending.release()


@app.get("/api/documents/{doc_id}/pdf")
def pdf(doc_id: str):
    doc = store.document(doc_id)
    if not doc:
        raise HTTPException(404, "文献不存在")
    return FileResponse(store.root / (doc["id"] + ".pdf"), media_type="application/pdf")


class Question(BaseModel):
    question: str = Field(min_length=1, max_length=1500)
    document_ids: list[str] = Field(default_factory=list, max_length=50)


@app.post("/api/chat")
async def chat(request: Question):
    if chat_lock.locked():
        raise HTTPException(429, "模型正在回答另一个问题，请稍后重试")
    async with chat_lock:
        rows = await asyncio.to_thread(store.rows, request.document_ids)
        if not rows:
            return {
                "answer": "当前范围没有已解析的文献，请先导入 PDF 并等待索引完成。",
                "citations": [],
                "insufficient_evidence": True,
                "steps": ["检查文献范围"],
            }
        if models.llm is None:
            raise HTTPException(503, "本地模型未就绪，请检查服务器模型配置")
        search_question = await asyncio.to_thread(models.search_query, request.question)
        vectors = await asyncio.to_thread(models.encode, [search_question], True)
        results = retrieve(rows, search_question, vectors[0] if vectors else None)
        evidence = [dict(r, label=f"E{i}") for i, r in enumerate(results, 1)]
        steps = [
            "检查文献范围",
            "生成中英检索词",
            "混合检索" if vectors else "BM25 检索",
            "本地模型阅读证据",
            "校验原文摘录与数值",
        ]
        if not evidence:
            return {
                "answer": "未检索到相关原文，请尝试论文中的关键词或扩大文献范围。",
                "citations": [],
                "insufficient_evidence": True,
                "steps": steps[:2],
            }
        try:
            raw = await asyncio.to_thread(models.answer, request.question, evidence)
            answer, labels, refused = validate_answer(raw, evidence)
        except (ValueError, RuntimeError) as exc:
            log.warning("Answer rejected: %s", exc)
            return {
                "answer": "本次模型回答未通过格式或引用校验，暂不展示。请换一种问法，或直接核对下方原文。",
                "citations": [citation(e) for e in evidence],
                "insufficient_evidence": True,
                "steps": steps,
                "validation_error": str(exc)[:200],
            }
        return {
            "answer": answer,
            "citations": [
                citation(
                    e,
                    "\n".join(c["quote"] for c in raw["claims"] if c["source"] == e["label"]),
                )
                for e in evidence
                if e["label"] in labels
            ],
            "insufficient_evidence": refused,
            "steps": steps,
        }


def citation(e, quote=None):
    return {
        "id": e["label"],
        "document_id": e["document_id"],
        "title": e["title"],
        "page": e["page"],
        "quote": quote or e["text"],
        "url": f"/api/documents/{e['document_id']}/pdf#page={e['page']}",
    }


dist = Path(os.getenv("YANXI_DIST_DIR", "dist"))
if dist.is_dir():
    app.mount("/", StaticFiles(directory=dist, html=True), name="frontend")
