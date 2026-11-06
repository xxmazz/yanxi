import asyncio
import logging
import os
import re
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

from .access import AccessSettings, ReviewAccess
from .architecture import architecture_comparison
from .bibliography import author_ordinal, ordered_author
from .comparison import explicit_configuration_facts
from .conversation import context_scope
from .core import Store, chunks_for_page, retrieve, validate_answer
from .discovery import ArxivDiscovery, DiscoveryError, rank_topic_candidates, valid_id
from .following import Following
from .fulltext_check import evidence_for as prepare_fulltext_check
from .future import prepare as prepare_future
from .graph import MAX_DOCUMENTS, build_graph
from .metric_tables import bleu_column, read_bleu_table, table_answer
from .models import LocalModels
from .parsing import LocalOCR, extract_page, metadata, page_payload
from .reading_links import navigation
from .reference_resolution import ReferenceResolver
from .research_tasks import ResearchTasks
from .structure import analyze as analyze_structure
from .structure import table_csv
from .translation import TranslationService
from .visual_structure import VisualStructure
from .writing import prepare as prepare_writing
from .writing import render_draft

log = logging.getLogger("yanxi")
access_settings = AccessSettings.from_env()
store = Store(Path(os.getenv("YANXI_DATA_DIR", "runtime/library")))
models = LocalModels()
ocr = LocalOCR()
discovery = ArxivDiscovery(store)
translation = TranslationService(store, models)
research_tasks = ResearchTasks(store)
following = Following(store)
reference_resolver = ReferenceResolver(store)
visual_structure = VisualStructure()
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
            if source.get("title", "").strip():
                info["title"] = source["title"].strip()[:300]
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
    research_tasks.recover()
    if models.llm_path or models.embedding_path:
        await asyncio.to_thread(models.load)
    if ocr.path:
        await asyncio.to_thread(ocr.load)
    if visual_structure.path:
        await asyncio.to_thread(visual_structure.load)
    # 宕机前排队的任务重新提交，避免永久停在 queued 状态。
    for doc in store.documents():
        if doc["status"] in ("queued", "processing"):
            if pending.acquire(blocking=False):
                workers.submit(index_document, doc["id"])
            else:
                store.fail(doc["id"], "重启恢复队列已满，请重新导入")
    discovery.recover_jobs()
    translation.recover()
    following.recover()
    follow_scheduler = asyncio.create_task(following.scheduler(follow_search))
    yield
    follow_scheduler.cancel()
    await asyncio.gather(follow_scheduler, return_exceptions=True)
    await following.shutdown()
    await research_tasks.shutdown()
    await asyncio.to_thread(translation.shutdown)
    download_workers.shutdown(wait=True)
    workers.shutdown(wait=True)


app = FastAPI(title="研析本地科研助手 API", lifespan=lifespan)
app.add_middleware(ReviewAccess, settings=access_settings)


@app.get("/api/health")
def health():
    return {
        "status": "ok",
        "model_ready": models.llm is not None,
        "embedding_ready": models.encoder is not None,
        "model": Path(models.llm_path).name or None,
        "retrieval": "bm25+e5-rrf" if models.encoder is not None else "bm25",
        "ocr_ready": ocr.reader is not None,
        "vision_ready": visual_structure.model is not None,
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
@app.head("/api/documents/{doc_id}/pdf", include_in_schema=False)
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


@app.get("/api/documents/{doc_id}/reading-links")
def reading_links(doc_id: str):
    return document_action(lambda: navigation(store, doc_id))


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


class ArchiveClear(BaseModel):
    document_ids: list[str] = Field(min_length=1, max_length=1000)


@app.post("/api/documents/archive/clear")
def clear_archive(request: ArchiveClear):
    with upload_lock:
        return document_action(lambda: store.clear_archived(request.document_ids))


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


@app.get("/api/documents/{doc_id}/pages/{number}/structure")
def page_structure(doc_id: str, number: int):
    return document_action(lambda: analyze_structure(store, doc_id, number))


class VisualStructureRequest(BaseModel):
    kind: Literal["table", "equation"]
    bbox: list[float] = Field(min_length=4, max_length=4)
    refresh: bool = False
    expected_columns: int = Field(default=0, ge=0, le=24)


@app.post("/api/documents/{doc_id}/pages/{number}/visual-structure")
def visual_page_structure(doc_id: str, number: int, request: VisualStructureRequest):
    try:
        return visual_structure.extract(
            store,
            doc_id,
            number,
            request.kind,
            request.bbox,
            request.refresh,
            request.expected_columns,
        )
    except OverflowError as exc:
        raise HTTPException(429, str(exc)) from exc
    except KeyError as exc:
        raise HTTPException(404, str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(409, str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(503, str(exc)) from exc


def structure_item(doc_id, number, item_id):
    payload = document_action(lambda: analyze_structure(store, doc_id, number))
    item = next((item for item in payload["items"] if item["id"] == item_id), None)
    if item is None:
        raise HTTPException(404, "本页没有此结构项")
    return item


@app.get("/api/documents/{doc_id}/pages/{number}/structure/{item_id}/image")
def structure_image(doc_id: str, number: int, item_id: str):
    import io

    from PIL import Image

    item = structure_item(doc_id, number, item_id)
    rendered = render_page(doc_id, number)
    with Image.open(rendered.path) as image:
        x0, y0, x1, y1 = item["bbox"]
        crop = image.crop(
            (
                int(x0 * image.width),
                int(y0 * image.height),
                int(x1 * image.width + 0.999),
                int(y1 * image.height + 0.999),
            )
        )
        output = io.BytesIO()
        crop.save(output, format="PNG")
    return Response(output.getvalue(), media_type="image/png")


@app.get("/api/documents/{doc_id}/pages/{number}/structure/{item_id}/csv")
def structure_csv(doc_id: str, number: int, item_id: str):
    item = structure_item(doc_id, number, item_id)
    if item["kind"] != "table":
        raise HTTPException(404, "此项不是表格")
    return Response(
        table_csv(item),
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="table-p{number}-{item_id}.csv"'},
    )


class Question(BaseModel):
    question: str = Field(min_length=1, max_length=1500)
    document_ids: list[str] = Field(default_factory=list, max_length=50)
    previous_document_ids: list[str] = Field(default_factory=list, max_length=6)


@app.post("/api/chat")
async def chat(request: Question):
    scope, continued = context_scope(
        request.question, request.document_ids, request.previous_document_ids
    )
    if continued:
        # 不允许文献失效后悄悄退回全库，原文仍需经过有效状态检查。
        unavailable = [
            doc_id
            for doc_id in scope
            if not (doc := store.document(doc_id))
            or doc["status"] != "ready"
            or not doc["is_active"]
            or doc["trashed_at"]
        ]
        if unavailable:
            return {
                "answer": "上次回答的文献已不可用，请重新选择文献范围后提问。",
                "citations": [],
                "insufficient_evidence": True,
                "steps": ["核对追问文献状态"],
                "context_used": False,
            }
    result = await answer_question(request.model_copy(update={"document_ids": scope}))
    return {
        **result,
        "context_used": continued,
        "steps": (["沿用上次回答的原文范围，重新检索证据"] if continued else []) + result["steps"],
    }


async def answer_question(request: Question):
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
        coverage_ids = None
        if len(set(request.document_ids)) > 1 and re.search(
            r"对比|比较|相比|相较|承袭|区别|变化|分别|compare|comparison|versus|\bvs\.?\b",
            request.question,
            re.I,
        ):
            coverage_ids = list(dict.fromkeys(request.document_ids))
            available = {row["document_id"] for row in rows}
            if len(coverage_ids) > 6 or not set(coverage_ids) <= available:
                return {
                    "answer": "对比需要选择不超过六篇已就绪的有效文献，请检查归档、解析状态及所选范围。",
                    "citations": [],
                    "insufficient_evidence": True,
                    "steps": ["检查对比文献范围"],
                }
        search_question = await asyncio.to_thread(models.search_query, request.question)
        vectors = await asyncio.to_thread(models.encode, [search_question], True)
        results = retrieve(
            rows,
            search_question,
            vectors[0] if vectors else None,
            anchor_question=request.question,
            coverage_ids=coverage_ids,
        )
        evidence = [dict(r, label=f"E{i}") for i, r in enumerate(results, 1)]
        steps = [
            "检查文献范围",
            "生成中英检索词",
            "混合检索" if vectors else "BM25 检索",
            "本地模型阅读证据",
            "校验原文摘录与数值",
        ]
        async def explain_gap(reason):
            # 未核验的补充回答不附论文引用，也不能被后续任务当作有效摘要。
            try:
                text = await asyncio.to_thread(models.explain_gap, request.question, evidence)
            except (ValueError, RuntimeError) as exc:
                log.warning("Model explanation failed: %s", exc)
                raise HTTPException(503, "本次生成失败，请稍后重试") from exc
            return {
                "answer": text,
                "citations": [],
                "insufficient_evidence": True,
                "answer_mode": "model_explanation",
                "steps": steps + ["生成补充分析（暂无已核验的原文引用）"],
                "validation_error": reason[:200],
            }

        if not evidence:
            return await explain_gap("本次检索未找到相关原文")
        structural = (
            architecture_comparison(request.question, evidence)
            if coverage_ids and len(coverage_ids) == 2
            else None
        )
        if structural:
            return {
                "answer": structural["answer"],
                "citations": [citation(e) for e in structural["sources"]],
                "insufficient_evidence": False,
                "steps": [
                    "检查两篇文献范围",
                    "补召回架构段落",
                    "核对架构主语及组成",
                    "标注跨篇归纳与原页来源",
                ],
            }
        if bleu_column(request.question) and len({row["document_id"] for row in rows}) == 1:
            seen, answers = set(), []
            for item in evidence:
                if item["page"] in seen:
                    continue
                seen.add(item["page"])
                page = await asyncio.to_thread(store.page, item["document_id"], item["page"])
                if (
                    not page
                    or page["method"] != "native"
                    or "BLEU" not in page["text"]
                    or "Table" not in page["text"]
                ):
                    continue
                table = await asyncio.to_thread(
                    read_bleu_table,
                    store.root / (item["document_id"] + ".pdf"),
                    item["page"],
                    page["text"],
                )
                result = table_answer(request.question, table) if table else None
                if result:
                    answers.append((item, table, result))
            if len(answers) == 1:
                item, table, result = answers[0]
                return {
                    "answer": result["answer"],
                    "citations": [citation(dict(item, label="E1"), table["quote"])],
                    "calculation": result["calculation"],
                    "insufficient_evidence": result.get("insufficient_evidence", False),
                    "steps": [
                        "检查文献范围",
                        "定位表格原页",
                        "按坐标核对 BLEU 列与模型行",
                        "计算并标注来源数值差值",
                    ],
                }
        try:
            if coverage_ids:
                claims = []
                max_claims = min(2, 8 // len(coverage_ids))
                for doc_id in coverage_ids:
                    subset = [e for e in evidence if e["document_id"] == doc_id]
                    single = explicit_configuration_facts(request.question, subset)
                    if single is None or len(single["claims"]) > max_claims:
                        single = await asyncio.to_thread(
                            models.comparison_facts, request.question, subset, max_claims=max_claims
                        )
                    try:
                        _, _, single_refused = validate_answer(single, subset)
                    except ValueError as exc:
                        raise ValueError(f"{exc}（{doc_id[:8]}）") from exc
                    if single_refused or not 1 <= len(single["claims"]) <= max_claims:
                        raise ValueError(f"一篇所选文献未提供可核对的相关事实（{doc_id[:8]}）")
                    claims.extend(single["claims"])
                raw = {"claims": claims, "insufficient_evidence": False}
                steps = [
                    "检查对比文献范围",
                    "按比较维度逐篇召回",
                    "逐篇核对原文事实",
                    "校验原文数值及引用覆盖",
                ]
            else:
                # 单篇或普通问答有一次有界重读；不放宽原文、数值和引用检查。
                retry_note = ""
                for attempt in range(2):
                    try:
                        if attempt:
                            raw = await asyncio.to_thread(
                                models.answer, request.question, evidence, retry_note=retry_note
                            )
                        else:
                            raw = await asyncio.to_thread(models.answer, request.question, evidence)
                        _, _, refused = validate_answer(raw, evidence)
                        if not refused:
                            break
                        retry_note = "上一轮认为原文依据不足，请重新核对相关章节与作者的后续工作"
                    except (ValueError, RuntimeError) as exc:
                        retry_note = str(exc)[:200]
                    if attempt == 0:
                        steps.append("重读相关原文并重新生成")
                else:
                    return await explain_gap(retry_note)
            answer, labels, refused = validate_answer(raw, evidence)
            if coverage_ids and not refused:
                cited_docs = {e["document_id"] for e in evidence if e["label"] in labels}
                if not set(coverage_ids) <= cited_docs:
                    raise ValueError("对比回答没有覆盖全部所选文献来源")
        except (ValueError, RuntimeError) as exc:
            log.warning("Answer rejected: %s", exc)
            return await explain_gap(str(exc))
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


class SelectionTranslation(BaseModel):
    target: Literal["zh", "en"] = "zh"
    start: int = Field(ge=0, le=500000)
    end: int = Field(ge=1, le=500000)
    text: str = Field(min_length=1, max_length=800)


@app.post("/api/documents/{doc_id}/translation/selection/{number}", status_code=202)
def translate_selection(doc_id: str, number: int, request: SelectionTranslation):
    return translation_action(
        lambda: translation.start_selection(
            doc_id, number, request.start, request.end, request.text, request.target
        )
    )


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
    document_ids: list[str] = Field(default_factory=list, max_length=MAX_DOCUMENTS)


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
            "请为当前这篇论文只写一条简短中文结论，概括摘要中明确陈述的问题、方法或结论。"
            "每条结论只描述所引用的单篇原文，勿补充背景常识、作者未报告的实验或推断方法继承关系。"
            "特别注意否定条件的对象：without ground truth human annotations 应写为不依赖人工标注的标准答案，"
            "不能改写成无需参考文本或无需检索上下文；reference-free evaluation 也不等于无需检索上下文。"
            "不要评价论文创新性，不要声称未来方向尚未被后续研究解决。保留原文对效果的限定条件。"
            "使用第三人称，不要把论文的工作写成‘我们’的工作。"
            "涉及最佳或优于的比较，必须同时保留原文的比较范围、数据集及指标；"
            "无法简短保留限定条件时，改为描述论文方法，不写排名结论。"
        )
        try:
            # 六篇一起生成时小模型可能漏掉后面的来源；逐篇约束范围，
            # 每篇先通过原文／数值检查，再合并，任何一篇失败均不展示半份综述。
            claims = []
            for item in evidence:
                part = await asyncio.to_thread(models.answer, question, [item])
                _, part_labels, part_refused = validate_answer(part, [item])
                if part_refused or part_labels != [item["label"]] or len(part["claims"]) != 1:
                    raise ValueError("单篇摘要没有返回唯一且有来源的结论")
                claims.extend(part["claims"])
            raw = {"claims": claims, "insufficient_evidence": False}
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


class FutureCheck(GraphReview):
    topic: str = Field(min_length=3, max_length=240)


@app.post("/api/graph/future-check")
async def future_check(request: FutureCheck):
    if len(request.topic.strip()) < 3:
        raise HTTPException(422, "请输入至少三个字符的研究主题")
    graph = document_action(lambda: build_graph(store, request.document_ids))
    if graph["fingerprint"] != request.fingerprint:
        raise HTTPException(409, "原文范围已变化，请重新构图")
    if not any(node["future_work"] for node in graph["nodes"]):
        raise HTTPException(409, "当前范围没有明确的后续研究原文")
    if models.llm is None:
        raise HTTPException(503, "本地模型未就绪")
    searched = await search_papers(q=request.topic, start=0, sort="newest")
    if searched["items"] and models.encoder is not None:
        items = searched["items"]
        query_vector = await asyncio.to_thread(
            models.encode, [searched["search_query"]], query=True
        )
        vectors = await asyncio.to_thread(
            models.encode, [item["title"] + " " + item["abstract"][:1900] for item in items]
        )
        if query_vector is not None and vectors is not None:
            searched = {**searched, "items": rank_topic_candidates(items, query_vector[0], vectors)}
    report = document_action(lambda: prepare_future(graph, searched))
    if chat_lock.locked():
        raise HTTPException(429, "本地模型正在处理其他请求，请稍后重试")
    comparisons = []
    async with chat_lock:
        for item in report["candidates"]:
            question = (
                f"拟研究主题：{request.topic}。请只用一句简短中文概括当前摘要中与主题相关的"
                "方法或研究对象；若联系不明确，说明摘要无法支持联系。"
                "只描述本篇摘要陈述的工作，不推断它已经解决或没有解决其他论文的未来方向，"
                "不声称研究主题首次被提出，不写摘要没有的指标或实验。"
            )
            try:
                raw = await asyncio.to_thread(models.answer, question, [item])
                answer, labels, refused = validate_answer(raw, [item])
                comparisons.append(
                    {
                        "source": item["label"],
                        "answer": answer,
                        "supported": not refused and labels == [item["label"]],
                    }
                )
            except (ValueError, RuntimeError):
                comparisons.append(
                    {
                        "source": item["label"],
                        "answer": "摘要概括未通过来源检查，请直接核对摘要。",
                        "supported": False,
                    }
                )
    current = document_action(lambda: build_graph(store, request.document_ids))
    if current["fingerprint"] != request.fingerprint:
        raise HTTPException(409, "核对期间原文已变化，请重新构图")
    return {**report, "topic": request.topic, "comparisons": comparisons}


class FulltextCheck(FutureCheck):
    target_document_ids: list[str] = Field(min_length=1, max_length=4)


@app.post("/api/graph/fulltext-check")
async def fulltext_check(request: FulltextCheck):
    if chat_lock.locked():
        raise HTTPException(429, "本地模型正在处理其他请求，请稍后重试")
    async with chat_lock:
        graph = document_action(lambda: build_graph(store, request.document_ids))
        if graph["fingerprint"] != request.fingerprint:
            raise HTTPException(409, "原方向文献已变化，请重新构图")
        targets = document_action(lambda: build_graph(store, request.target_document_ids))
        if models.llm is None or models.encoder is None:
            raise HTTPException(503, "本地生成及向量模型需同时就绪")
        query = await asyncio.to_thread(models.discovery_query, request.topic)
        vector = await asyncio.to_thread(models.encode, [query], query=True)
        report = document_action(
            lambda: prepare_fulltext_check(store, graph, targets, query, vector[0])
        )
        report["topic"] = request.topic
        report["retrieval_query"] = query
        for group in report["groups"]:
            evidence = group["evidence"]
            group.update(
                supported=False, answer="正文检索未获得足够证据，请核对论文原文。", citations=[]
            )
            if not evidence:
                continue
            question = (
                f"拟议方向：{request.topic}。只根据所给本篇正文，用一至两条中文陈述本篇与方向有关的研究方法、对象或限制。"
                "不要推断已经彻底解决其他论文的未来方向，也不要声称首次提出。保留原文的限定条件与研究对象。"
                "reference-free或without human annotations指不依赖人工标注标准答案，不等于不需要检索参考文本。"
            )
            try:
                raw = await asyncio.to_thread(models.answer, question, evidence)
                answer, labels, refused = validate_answer(raw, evidence)
                group.update(
                    answer=answer,
                    supported=not refused and bool(labels),
                    citations=[
                        {
                            **citation(item),
                            "start": item["start"],
                            "end": item["end"],
                            "page_sha256": item["page_sha256"],
                        }
                        for item in evidence
                        if item["label"] in labels
                    ],
                )
            except (ValueError, RuntimeError):
                group["answer"] = "本篇正文概括未通过来源或数值核对，请直接查看原句。"
        current = document_action(lambda: build_graph(store, request.document_ids))
        target_current = document_action(lambda: build_graph(store, request.target_document_ids))
        if (
            current["fingerprint"] != request.fingerprint
            or target_current["fingerprint"] != targets["fingerprint"]
        ):
            raise HTTPException(409, "核对期间原文或对照论文发生变化，请重新选择")
        return report


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


class ResearchTaskRequest(BaseModel):
    query: str = Field(min_length=2, max_length=240)
    limit: int = Field(default=2, ge=1, le=3)


async def task_search(query):
    response = await search_papers(q=query, start=0, sort="relevance")
    response["selection_method"] = "official_relevance"
    if response["items"] and models.encoder is not None:
        query_vectors = await asyncio.to_thread(
            models.encode, [response["search_query"]], query=True
        )
        vectors = await asyncio.to_thread(
            models.encode,
            [item["title"] + " " + item["abstract"][:1800] for item in response["items"]],
        )
        if query_vectors is None or vectors is None:
            raise ValueError("本地主题重排未返回向量，请重试")
        response["items"] = rank_topic_candidates(response["items"], query_vectors[0], vectors)
        response["selection_method"] = "e5_topic"
        response["steps"].append("本地 E5 按本次主题重排当前页候选")
    return response


async def task_import(arxiv_id):
    return import_paper(ImportPaper(arxiv_id=arxiv_id))


async def task_summary(ids):
    if models.llm is None:
        return {
            "answer": "本地模型未启动，已完成原文入库与来源汇总；启动模型后可在引用图谱中生成摘要综述。",
            "citations": [],
            "insufficient_evidence": True,
        }
    graph = await asyncio.to_thread(build_graph, store, ids)
    return await graph_review(GraphReview(document_ids=ids, fingerprint=graph["fingerprint"]))


def start_research(task_id):
    research_tasks.start(
        task_id, search=task_search, submit=task_import, job=discovery.job, summarize=task_summary
    )


@app.get("/api/research/tasks")
def list_research_tasks():
    fingerprints = {}
    return [research_tasks.view(task, fingerprints) for task in research_tasks.list()]


@app.post("/api/research/tasks", status_code=202)
async def create_research_task(request: ResearchTaskRequest):
    if len(request.query.strip()) < 2:
        raise HTTPException(422, "请输入至少两个字符的研究主题")
    task = document_action(lambda: research_tasks.create(request.query.strip(), request.limit))
    start_research(task["id"])
    return task


@app.post("/api/research/tasks/{task_id}/retry", status_code=202)
async def retry_research_task(task_id: str):
    if task_id in research_tasks.running:
        raise HTTPException(409, "任务仍在结束中，请稍后重试")

    def retry_current():
        stored = research_tasks.get(task_id)
        view = research_tasks.view(stored)
        return research_tasks.retry(task_id, refresh_summary=view.get("summary_stale", False))

    task = document_action(retry_current)
    start_research(task_id)
    return task


@app.post("/api/research/tasks/{task_id}/cancel")
async def cancel_research_task(task_id: str):
    return document_action(lambda: research_tasks.cancel(task_id))


@app.get("/api/research/tasks/{task_id}")
def get_research_task(task_id: str):
    return document_action(lambda: research_tasks.view(research_tasks.get(task_id)))


async def follow_search(query):
    return await search_papers(q=query, start=0, sort="newest")


class SubscriptionRequest(BaseModel):
    query: str = Field(min_length=2, max_length=240)
    cadence: Literal["daily", "weekly"] = "daily"


class SubscriptionEnabled(BaseModel):
    enabled: bool


class FeedbackRequest(BaseModel):
    base_id: str = Field(max_length=60)
    value: Literal[-1, 0, 1]


@app.get("/api/following")
def list_following():
    return {"subscriptions": following.list("subscription"), "batches": following.list("batch")}


@app.post("/api/following/subscriptions", status_code=201)
def create_subscription(request: SubscriptionRequest):
    if not request.query.strip():
        raise HTTPException(422, "订阅主题不能为空")
    return document_action(lambda: following.subscribe(request.query, request.cadence))


@app.post("/api/following/{identifier}/enabled")
def enable_subscription(identifier: str, request: SubscriptionEnabled):
    return document_action(lambda: following.configure(identifier, request.enabled))


@app.post("/api/following/{identifier}/refresh")
async def refresh_subscription(identifier: str):
    try:
        return await following.refresh(identifier, follow_search)
    except KeyError as exc:
        raise HTTPException(404, str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(409, str(exc)) from exc


@app.post("/api/following/{identifier}/feedback")
def feedback_subscription(identifier: str, request: FeedbackRequest):
    return document_action(lambda: following.feedback(identifier, request.base_id, request.value))


@app.get("/api/documents/{doc_id}/reference-candidates")
def get_reference_candidates(doc_id: str):
    return document_action(lambda: reference_resolver.candidates(doc_id, navigation(store, doc_id)))


class ReferenceResolutionRequest(BaseModel):
    fingerprint: str = Field(min_length=64, max_length=64)
    reference: str = Field(min_length=1, max_length=160)


@app.post("/api/documents/{doc_id}/reference-resolutions")
async def resolve_reference(doc_id: str, request: ReferenceResolutionRequest):
    if search_lock.locked():
        raise HTTPException(429, "正在检索其他论文，请稍后匹配")
    async with search_lock:
        data = await asyncio.to_thread(document_action, lambda: navigation(store, doc_id))
        if data["fingerprint"] != request.fingerprint:
            raise HTTPException(409, "原文已变化，请重新读取参考文献")
        try:
            result = await asyncio.to_thread(
                reference_resolver.resolve, doc_id, data, request.reference, discovery
            )
        except DiscoveryError as exc:
            raise HTTPException(exc.status, str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(409, str(exc)) from exc
        current = await asyncio.to_thread(document_action, lambda: navigation(store, doc_id))
        if current["fingerprint"] != request.fingerprint:
            raise HTTPException(409, "检索期间原文已变化，请重新读取参考文献")
        reference_resolver.save(doc_id, request.fingerprint, result)
        return result


class ReferenceBatchRequest(BaseModel):
    fingerprint: str = Field(min_length=64, max_length=64)
    arxiv_ids: list[str] = Field(min_length=1, max_length=30)


@app.post("/api/documents/{doc_id}/reference-batches", status_code=202)
async def start_reference_batch(doc_id: str, request: ReferenceBatchRequest):
    def create():
        candidates = reference_resolver.candidates(doc_id, navigation(store, doc_id))
        if candidates["fingerprint"] != request.fingerprint:
            raise ValueError("原文已变化，请重新读取参考文献")
        lookup = {item["arxiv_id"]: item for item in candidates["items"]}
        ids = list(dict.fromkeys(request.arxiv_ids))
        if any(identifier not in lookup for identifier in ids):
            raise ValueError("只能下载已核对的参考文献编号")
        batch = following.batch(doc_id, [lookup[identifier] for identifier in ids])
        following.start_batch(batch["id"], task_import, discovery.job)
        return following.get(batch["id"])

    return document_action(create)


@app.post("/api/following/{identifier}/cancel")
def cancel_reference_batch(identifier: str):
    return document_action(lambda: following.cancel(identifier))


@app.post("/api/following/{identifier}/retry")
async def retry_reference_batch(identifier: str):
    def retry():
        following.start_batch(identifier, task_import, discovery.job)
        return following.get(identifier)

    return document_action(retry)


dist = Path(os.getenv("YANXI_DIST_DIR", "dist"))
if dist.is_dir():
    app.mount("/", StaticFiles(directory=dist, html=True), name="frontend")
