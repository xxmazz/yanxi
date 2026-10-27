import asyncio
import logging
import os
import subprocess
import tempfile
import threading
from concurrent.futures import ThreadPoolExecutor
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Literal

from fastapi import FastAPI, File, HTTPException, UploadFile
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field
from pypdf import PdfReader

from .core import Store, chunks_for_page, retrieve, validate_answer
from .models import LocalModels
from .parsing import LocalOCR, extract_page, metadata, page_payload

log = logging.getLogger("yanxi")
store = Store(Path(os.getenv("YANXI_DATA_DIR", "runtime/library")))
models = LocalModels()
ocr = LocalOCR()
workers = ThreadPoolExecutor(max_workers=1, thread_name_prefix="pdf-index")
upload_lock = threading.Lock()
chat_lock = asyncio.Lock()
pending = threading.BoundedSemaphore(8)


def index_document(doc_id):
    try:
        path = store.root / (doc_id + ".pdf")
        reader = PdfReader(path)
        if reader.is_encrypted:
            raise ValueError("加密 PDF 请先解密后再上传")
        if len(reader.pages) > 650:
            raise ValueError("当前支持不超过 650 页的 PDF")
        store.begin(doc_id, len(reader.pages))
        chunks, texts, first = [], [], None
        for number, page in enumerate(reader.pages, 1):
            parsed = extract_page(page, path, number, ocr)
            store.save_page(doc_id, number, parsed)
            if first is None:
                first = parsed
            texts.append(parsed["text"])
            chunks.extend(chunks_for_page(parsed["text"], number))
        if not chunks:
            raise ValueError("PDF 未识别到正文，请核对原文或扫描质量及 OCR 配置")
        vectors = models.encode([c["text"] for c in chunks])
        if vectors is not None:
            for chunk, vector in zip(chunks, vectors):
                chunk["vector"] = vector.tobytes()
        info = metadata(reader, first)
        analysis = store.analyze(doc_id, "\n".join(texts), chunks, info)
        store.finish(doc_id, len(reader.pages), chunks, info["title"], analysis, info)
    except Exception as exc:
        log.exception("PDF indexing failed: %s", doc_id)
        store.fail(doc_id, str(exc)[:300])
    finally:
        pending.release()


@asynccontextmanager
async def lifespan(app):
    if models.llm_path or models.embedding_path:
        await asyncio.to_thread(models.load)
    if ocr.path:
        await asyncio.to_thread(ocr.load)
    # 宕机前排队的任务重新提交，避免永久停在 queued 状态。
    for doc in store.documents():
        if doc["status"] in ("queued", "processing"):
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
        "ocr_ready": ocr.reader is not None,
    }


@app.get("/api/documents")
def documents(include_archived: bool = False):
    return store.documents(include_archived)


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
            if duplicate and (doc["trashed_at"] or not doc["is_active"]):
                raise HTTPException(409, "该文件已归档，请在归档记录中恢复")
            # 已失败的文档允许原文件重试（例如后来启用了 OCR）。
            if duplicate and doc["status"] == "error":
                store.queue(doc["id"])
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


def document_action(operation):
    try:
        return operation()
    except KeyError as exc:
        raise HTTPException(404, str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(409, str(exc)) from exc


@app.get("/api/documents/{doc_id}/pages/{number}")
def parsed_page(doc_id: str, number: int):
    page = store.page(doc_id, number)
    if page is None:
        raise HTTPException(404, "此页尚未解析，请等待完成或重新解析")
    return page_payload(page)


@app.post("/api/documents/{doc_id}/reindex", status_code=202)
def reindex(doc_id: str):
    if not pending.acquire(blocking=False):
        raise HTTPException(429, "解析队列已满，请稍后重试")
    try:
        document_action(lambda: store.queue(doc_id))
        workers.submit(index_document, doc_id)
    except Exception:
        pending.release()
        raise
    return store.document(doc_id)


@app.post("/api/documents/{doc_id}/archive")
def archive(doc_id: str):
    return document_action(lambda: store.trash(doc_id))


@app.post("/api/documents/{doc_id}/restore")
def restore(doc_id: str):
    return document_action(lambda: store.trash(doc_id, restore=True))


class VersionDecision(BaseModel):
    existing_id: str
    action: Literal["keep_both", "keep_existing", "replace_existing"]


@app.post("/api/documents/{doc_id}/resolve")
def resolve(doc_id: str, decision: VersionDecision):
    return document_action(lambda: store.resolve(doc_id, decision.existing_id, decision.action))


@app.get("/api/documents/{doc_id}/render/{number}")
def render_page(doc_id: str, number: int):
    doc = store.document(doc_id)
    if not doc or number < 1 or number > doc["pages"]:
        raise HTTPException(404, "文献或页码不存在")
    directory = store.root / "previews" / doc["id"]
    directory.mkdir(parents=True, exist_ok=True)
    cached = directory / f"{number}.png"
    if not cached.exists():
        with tempfile.TemporaryDirectory(dir=directory) as work:
            prefix = Path(work) / "page"
            try:
                subprocess.run(
                    [
                        "pdftoppm",
                        "-f",
                        str(number),
                        "-l",
                        str(number),
                        "-scale-to",
                        "1600",
                        "-singlefile",
                        "-png",
                        str(store.root / (doc["id"] + ".pdf")),
                        str(prefix),
                    ],
                    check=True,
                    capture_output=True,
                    timeout=120,
                )
                prefix.with_suffix(".png").replace(cached)
            except (OSError, subprocess.SubprocessError) as exc:
                log.warning("PDF preview failed: %s", exc)
                raise HTTPException(503, "原页预览生成失败，请使用原 PDF 链接") from exc
    return FileResponse(cached, media_type="image/png")


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
