import asyncio
import logging
import os
import subprocess
import tempfile
import threading
import uuid
from concurrent.futures import ThreadPoolExecutor
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Literal

from fastapi import FastAPI, File, HTTPException, Query, UploadFile
from fastapi.responses import FileResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field
from pypdf import PdfReader

from .bibliography import author_ordinal, ordered_author
from .core import Store, chunks_for_page, retrieve, validate_answer
from .discovery import ArxivDiscovery, DiscoveryError, valid_id
from .graph import build_graph
from .models import LocalModels
from .parsing import LocalOCR, extract_page, metadata, page_payload
from .translation import TranslationService
from .writing import prepare as prepare_writing
from .writing import render_draft

log = logging.getLogger("yanxi")
store = Store(Path(os.getenv("YANXI_DATA_DIR", "runtime/library")))
models = LocalModels()
ocr = LocalOCR()
discovery = ArxivDiscovery(store)
translation = TranslationService(store, models)
download_workers = ThreadPoolExecutor(max_workers=1, thread_name_prefix="arxiv-import")
download_slots = threading.BoundedSemaphore(2)
search_lock = asyncio.Lock()
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
        chunks, texts, warnings, first = [], [], [], None
        for number, page in enumerate(reader.pages, 1):
            parsed = extract_page(page, path, number, ocr)
            store.save_page(doc_id, number, parsed)
            if parsed.get("warning"):
                warnings.append(f"第 {number} 页：{parsed['warning']}")
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
        sources = discovery.sources(doc_id)
        if sources:
            source = sources[0]
            info["authors"] = "; ".join(source["authors"])[:1000]
            info["abstract"] = info["abstract"] or source["abstract"][:1800]
        analysis = store.analyze(doc_id, "\n".join(texts), chunks, info)
        store.finish(doc_id, len(reader.pages), chunks, info["title"], analysis, info, warnings)
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
    discovery.recover_jobs()
    translation.recover()
    yield
    await asyncio.to_thread(translation.shutdown)
    download_workers.shutdown(wait=True)
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


@app.get("/api/discovery/search")
async def search_papers(
    q: str = Query(min_length=2, max_length=240),
    start: int = Query(default=0, ge=0, le=992),
    sort: Literal["relevance", "newest", "library"] = "relevance",
):
    if search_lock.locked():
        raise HTTPException(429, "正在处理另一个论文检索，请稍后重试")
    async with search_lock:
        translated = q.strip()
        steps = ["读取检索主题"]
        if any("\u4e00" <= char <= "\u9fff" for char in q):
            if models.llm is None:
                raise HTTPException(503, "中文主题需要本地模型，请稍后重试或输入英文关键词")
            if chat_lock.locked():
                raise HTTPException(429, "本地模型正在回答问题，请稍后检索")
            async with chat_lock:
                try:
                    translated = await asyncio.to_thread(models.discovery_query, q)
                except (ValueError, RuntimeError) as exc:
                    raise HTTPException(
                        503, "本地模型检索词转换失败，请输入英文关键词重试"
                    ) from exc
            steps.append("将中文主题转换为英文检索词")
        try:
            result = await asyncio.to_thread(
                discovery.search, translated, start, "relevance" if sort == "library" else sort
            )
        except DiscoveryError as exc:
            raise HTTPException(exc.status, str(exc)) from exc
        steps.append("查询 arXiv 官方元数据" if not result["cached"] else "读取 arXiv 查询缓存")
        items = result["items"]
        library_ranked = False
        library = [d for d in store.documents() if d["status"] == "ready"]
        if library and items and models.encoder is not None:
            vectors = await asyncio.to_thread(
                models.encode,
                [d["title"] + " " + d["abstract"][:1800] for d in library[:40]]
                + [d["title"] + " " + d["abstract"][:1800] for d in items],
            )
            if vectors is not None:
                import numpy as np

                count = min(len(library), 40)
                base = np.asarray(vectors[:count])
                for item, vector in zip(items, vectors[count:]):
                    scores = base @ vector
                    index = int(np.argmax(scores))
                    item["related_document"] = {
                        "id": library[index]["id"],
                        "title": library[index]["title"],
                        "similarity": round(float(scores[index]), 4),
                    }
                library_ranked = True
                if sort == "library":
                    items.sort(
                        key=lambda item: item["related_document"]["similarity"], reverse=True
                    )
                steps.append("本地 E5 比较候选摘要与文献库主题")
        imported = discovery.imported()
        for item in items:
            item["imported"] = imported.get(item["arxiv_id"])
        return {
            **result,
            "query": q.strip(),
            "search_query": translated,
            "start": start,
            "page_size": 8,
            "sort": sort,
            "steps": steps,
            "library_count": len(library),
            "library_ranked": library_ranked,
        }


class ImportPaper(BaseModel):
    arxiv_id: str = Field(min_length=6, max_length=60)


def import_paper_job(job_id, arxiv_id):
    submitted = False
    try:
        discovery.update_job(job_id, "downloading")
        item = discovery.lookup(arxiv_id)
        existing = discovery.imported().get(item["arxiv_id"])
        if existing:
            doc = store.document(existing["document_id"])
            if existing["archived"]:
                raise DiscoveryError("该版本已归档，请在文献库的归档记录中恢复", 409)
            if doc["status"] != "error":
                discovery.update_job(job_id, "complete", doc["id"])
                return
        data = discovery.download(item)
        with upload_lock:
            doc, duplicate = store.create(data, item["arxiv_id"].replace("/", "_") + ".pdf")
            if doc["trashed_at"] or not doc["is_active"]:
                raise DiscoveryError("相同原文件已归档，请先从归档记录恢复", 409)
            discovery.source(doc["id"], item)
            if duplicate and doc["status"] == "error":
                store.queue(doc["id"])
                duplicate = False
            if not duplicate:
                workers.submit(index_document, doc["id"])
                submitted = True
        discovery.update_job(job_id, "complete", doc["id"])
    except Exception as exc:
        log.warning("arXiv import failed: %s", exc)
        message = (
            str(exc)
            if isinstance(exc, (DiscoveryError, ValueError))
            else "arXiv 下载失败，请稍后重试"
        )
        discovery.update_job(job_id, "error", error=message[:300])
    finally:
        if not submitted:
            pending.release()
        download_slots.release()


@app.post("/api/discovery/import", status_code=202)
def import_paper(request: ImportPaper):
    try:
        valid_id(request.arxiv_id)
    except DiscoveryError as exc:
        raise HTTPException(exc.status, str(exc)) from exc
    if not download_slots.acquire(blocking=False):
        raise HTTPException(429, "下载队列已满，请等待当前论文导入完成")
    if not pending.acquire(blocking=False):
        download_slots.release()
        raise HTTPException(429, "解析队列已满，请稍后导入")
    job_id = uuid.uuid4().hex
    try:
        discovery.create_job(job_id, request.arxiv_id)
        download_workers.submit(import_paper_job, job_id, request.arxiv_id)
    except Exception:
        pending.release()
        download_slots.release()
        raise
    return discovery.job(job_id)


@app.get("/api/discovery/jobs")
def import_jobs():
    return discovery.jobs()


@app.get("/api/documents/{doc_id}/sources")
def document_sources(doc_id: str):
    if not store.document(doc_id):
        raise HTTPException(404, "文献不存在")
    return discovery.sources(doc_id)


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
        ordinal = author_ordinal(request.question)
        if ordinal is not None:
            ids = list(dict.fromkeys(row["document_id"] for row in rows))
            details = None
            if len(ids) == 1:
                document = await asyncio.to_thread(store.document, ids[0])
                page = await asyncio.to_thread(store.page, ids[0], 1)
                details = ordered_author(document, page, ordinal)
            if details is None or details.get("out_of_range"):
                return {
                    "answer": "当前署名结构不足以可靠核对这个作者序号，请选择单篇文献并核对首页署名。",
                    "citations": [],
                    "insufficient_evidence": True,
                    "steps": ["检查文献范围", "核对首页署名顺序"],
                }
            source = {"label": "E1", "document_id": ids[0], "title": document["title"], "page": 1}
            institution = "；".join(details["affiliations"]) or "原文署名区未可靠识别到机构"
            answer = f"{details['name']}，署名机构：{institution}。[E1]"
            citations = [citation(source, details["quote"])]
            for index, note in enumerate(details["notes"], 2):
                label = f"E{index}"
                answer += f"\n\n署名脚注：{note}[{label}]"
                citations.append(citation(dict(source, label=label), note))
            return {
                "answer": answer,
                "citations": citations,
                "insufficient_evidence": False,
                "steps": ["检查文献范围", "核对首页署名顺序", "读取署名机构及脚注", "定位原页证据"],
            }
        if models.llm is None:
            raise HTTPException(503, "本地模型未就绪，请检查服务器模型配置")
        search_question = await asyncio.to_thread(models.search_query, request.question)
        vectors = await asyncio.to_thread(models.encode, [search_question], True)
        results = retrieve(
            rows,
            search_question,
            vectors[0] if vectors else None,
            anchor_question=request.question,
        )
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
                    # 多条结论可能共用一段证据；展示连续原段，避免重复或拼接
                    # 不相邻的摘录，导致界面引文无法在原页逐字定位。
                    next(c["quote"] for c in raw["claims"] if c["source"] == e["label"])
                    if sum(c["source"] == e["label"] for c in raw["claims"]) == 1
                    else e["text"],
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


def translation_action(operation):
    try:
        return operation()
    except KeyError as exc:
        raise HTTPException(404, str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(409, str(exc)) from exc
    except OverflowError as exc:
        raise HTTPException(429, str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(503, str(exc)) from exc


class DocumentTranslation(BaseModel):
    target: Literal["zh", "en"] = "zh"
    page: int | None = Field(default=None, ge=1, le=650)


class TextTranslation(BaseModel):
    target: Literal["zh", "en"] = "zh"
    text: str = Field(min_length=1, max_length=2400)


@app.post("/api/documents/{doc_id}/translation", status_code=202)
def translate_document(doc_id: str, request: DocumentTranslation):
    return translation_action(lambda: translation.start(request.target, doc_id, request.page))


@app.get("/api/documents/{doc_id}/translation/pages/{number}")
def translated_page(doc_id: str, number: int, target: Literal["zh", "en"] = "zh"):
    return translation_action(lambda: translation.page(doc_id, number, target))


@app.get("/api/documents/{doc_id}/translation/jobs")
def document_translation_jobs(doc_id: str):
    translation_action(lambda: translation.check_document(doc_id))
    with store.connect() as db:
        ids = db.execute(
            "SELECT id FROM translation_jobs WHERE document_id=? ORDER BY rowid DESC LIMIT 8",
            (doc_id,),
        ).fetchall()
    return [translation.job(row["id"]) for row in ids]


@app.post("/api/translation/text", status_code=202)
def translate_text(request: TextTranslation):
    if not request.text.strip():
        raise HTTPException(422, "请输入需要翻译的文本")
    return translation_action(lambda: translation.start(request.target, text=request.text))


@app.get("/api/translation/jobs/{job_id}")
def translation_job(job_id: str):
    return translation_action(lambda: translation.job(job_id))


@app.post("/api/translation/jobs/{job_id}/cancel")
def cancel_translation(job_id: str):
    return translation_action(lambda: translation.cancel(job_id))


@app.get("/api/documents/{doc_id}/translation/export")
def export_translation(doc_id: str, target: Literal["zh", "en"] = "zh"):
    content = translation_action(lambda: translation.export(doc_id, target))
    return Response(
        content,
        media_type="text/markdown; charset=utf-8",
        headers={
            "Content-Disposition": f'attachment; filename="translation-{doc_id[:12]}-{target}.md"'
        },
    )


class GraphScope(BaseModel):
    document_ids: list[str] = Field(default_factory=list, max_length=30)


@app.post("/api/graph/build")
def citation_graph(request: GraphScope):
    try:
        return build_graph(store, request.document_ids)
    except ValueError as exc:
        raise HTTPException(409, str(exc)) from exc


class GraphReview(GraphScope):
    fingerprint: str = Field(min_length=64, max_length=64)


@app.post("/api/graph/review")
async def graph_review(request: GraphReview):
    if chat_lock.locked():
        raise HTTPException(429, "本地模型正在处理其他请求，请稍后生成综述")
    async with chat_lock:
        try:
            graph = await asyncio.to_thread(build_graph, store, request.document_ids)
        except ValueError as exc:
            raise HTTPException(409, str(exc)) from exc
        if graph["fingerprint"] != request.fingerprint:
            raise HTTPException(409, "文献范围或原文已变化，请先重新构图")
        if models.llm is None:
            raise HTTPException(503, "本地模型未就绪")
        ranked = sorted(graph["nodes"], key=lambda node: (-node["pagerank"], node["id"]))
        evidence = []
        for node in ranked:
            item = node["abstract_evidence"]
            if item and item["quote"].strip() and len(evidence) < 6:
                evidence.append(
                    {
                        "label": f"E{len(evidence) + 1}",
                        "document_id": item["document_id"],
                        "title": node["title"],
                        "page": item["page"],
                        "text": item["quote"],
                    }
                )
        if not evidence:
            return {
                "fingerprint": graph["fingerprint"],
                "answer": "所选文献没有可核对的摘要原文，暂不生成综述。",
                "citations": [],
                "insufficient_evidence": True,
                "covered_documents": [],
            }
        question = (
            f"请按顺序为这 {len(evidence)} 个来源各写一条中文结论，概括该论文研究的问题、方法和结论，形成简短领域综述。"
            f"必须覆盖全部来源：{', '.join(item['label'] for item in evidence)}，不要只描述第一篇论文。"
            "每条结论只描述所引用的单篇原文，勿补充背景常识、作者未报告的实验或推断方法继承关系。"
            "特别注意否定条件的对象：without ground truth human annotations 应写为不依赖人工标注的标准答案，"
            "不能改写成无需参考文本或无需检索上下文；reference-free evaluation 也不等于无需检索上下文。"
            "不要评价论文创新性，不要声称未来方向尚未被后续研究解决。保留原文对效果的限定条件。"
        )
        try:
            raw = await asyncio.to_thread(models.answer, question, evidence)
            answer, labels, refused = validate_answer(raw, evidence)
            if not refused and set(labels) != {item["label"] for item in evidence}:
                raise ValueError("综述没有覆盖本轮摘要来源")
        except (ValueError, RuntimeError) as exc:
            log.warning("Graph review rejected: %s", exc)
            return {
                "fingerprint": graph["fingerprint"],
                "answer": "本次综述未通过来源、数值或摘要覆盖范围校验，请重试或直接查看原文。",
                "citations": [citation(item) for item in evidence],
                "insufficient_evidence": True,
                "covered_documents": [item["document_id"] for item in evidence],
            }
        try:
            current_graph = await asyncio.to_thread(build_graph, store, request.document_ids)
        except ValueError as exc:
            raise HTTPException(409, "生成期间文献状态已变化，请重新构图") from exc
        if current_graph["fingerprint"] != graph["fingerprint"]:
            raise HTTPException(409, "生成期间文献原文已变化，请重新构图")
        return {
            "fingerprint": graph["fingerprint"],
            "answer": answer,
            "citations": [citation(item) for item in evidence if item["label"] in labels],
            "insufficient_evidence": refused,
            "covered_documents": [item["document_id"] for item in evidence],
        }


class WritingRequest(BaseModel):
    idea: str = Field(min_length=3, max_length=1200)
    document_ids: list[str] = Field(min_length=1, max_length=30)


class WritingCheck(BaseModel):
    document_ids: list[str] = Field(min_length=1, max_length=30)
    fingerprint: str = Field(min_length=64, max_length=64)


async def check_writing_sources(document_ids, fingerprint):
    try:
        current = await asyncio.to_thread(build_graph, store, document_ids)
    except ValueError as exc:
        raise HTTPException(409, "写作来源状态已变化，请重新选择文献") from exc
    if current["fingerprint"] != fingerprint:
        raise HTTPException(409, "写作原文或书目信息已变化，请重新生成并核对引用")
    return {"fingerprint": fingerprint, "current": True}


@app.post("/api/writing/check")
async def writing_check(request: WritingCheck):
    return await check_writing_sources(request.document_ids, request.fingerprint)


@app.post("/api/writing/outline")
async def writing_outline(request: WritingRequest):
    idea = request.idea.strip()
    if len(idea) < 3:
        raise HTTPException(422, "请填写至少三个字符的研究设想")
    if chat_lock.locked():
        raise HTTPException(429, "本地模型正在处理其他请求，请稍后生成研究计划")
    async with chat_lock:
        try:
            bundle = await asyncio.to_thread(prepare_writing, store, request.document_ids)
        except ValueError as exc:
            raise HTTPException(409, str(exc)) from exc
        if models.llm is None:
            raise HTTPException(503, "本地模型未就绪")
        try:
            raw = await asyncio.to_thread(models.draft_outline, idea, bundle["evidence"])
            result = await asyncio.to_thread(render_draft, idea, raw, bundle)
        except (ValueError, RuntimeError) as exc:
            log.warning("Writing output rejected: %s", exc)
            raise HTTPException(
                422, "本次写作未通过结构、来源或数值检查，请重试；现有草稿仍保留"
            ) from exc
        await check_writing_sources(bundle["document_ids"], bundle["fingerprint"])
        return result


dist = Path(os.getenv("YANXI_DIST_DIR", "dist"))
if dist.is_dir():
    app.mount("/", StaticFiles(directory=dist, html=True), name="frontend")
