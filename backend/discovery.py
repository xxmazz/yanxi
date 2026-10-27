"""arXiv 官方 Atom 检索、受限 PDF 下载与本地库相关性排序。"""

import json
import re
import threading
import time
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from urllib.parse import urlparse

import httpx

API_URL = "https://export.arxiv.org/api/query"
ARXIV_ID = r"(?:\d{4}\.\d{4,5}|[a-z][a-z.\-]+/\d{7})(?:v[1-9]\d*)?"
NAMESPACES = {
    "a": "http://www.w3.org/2005/Atom",
    "op": "http://a9.com/-/spec/opensearch/1.1/",
    "arxiv": "http://arxiv.org/schemas/atom",
}
CACHE_SECONDS = 6 * 3600


class DiscoveryError(Exception):
    def __init__(self, message, status=503):
        super().__init__(message)
        self.status = status


def valid_id(value):
    if not re.fullmatch(ARXIV_ID, value, re.I):
        raise DiscoveryError("无效的 arXiv 编号", 400)
    return value


def query_expression(text):
    text = text.strip()
    candidate = re.sub(r"^arxiv\s*:\s*", "", text, flags=re.I)
    if re.fullmatch(ARXIV_ID, candidate, re.I):
        return {"id_list": candidate}
    words = re.findall(r"[a-zA-Z0-9]+(?:-[a-zA-Z0-9]+)*", text)
    stopwords = {
        "a",
        "an",
        "the",
        "is",
        "and",
        "of",
        "on",
        "for",
        "in",
        "with",
        "papers",
        "paper",
        "about",
    }
    terms = list(dict.fromkeys(w.lower() for w in words if w.lower() not in stopwords))[:16]
    if not terms:
        raise DiscoveryError("请输入英文主题、论文标题或 arXiv 编号；中文主题需要本地模型转换", 400)
    # 按词构造字段表达式，不把用户文本直接作为 API 语法执行。
    return {"search_query": " AND ".join(f'all:"{term}"' for term in terms)}


def github_repositories(text):
    links = re.findall(r"https?://github\.com/[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", text)
    return list(
        dict.fromkeys("https://" + link.split("://", 1)[1].rstrip(".,;)") for link in links)
    )[:5]


def parse_feed(content):
    if b"<!DOCTYPE" in content.upper() or b"<!ENTITY" in content.upper():
        raise DiscoveryError("arXiv 返回了不支持的 XML 内容")
    try:
        root = ET.fromstring(content)
        if root.tag != f"{{{NAMESPACES['a']}}}feed":
            raise ValueError("not Atom")
        total = int(root.findtext("op:totalResults", "0", NAMESPACES))
        items = []
        for entry in root.findall("a:entry", NAMESPACES):

            def value(path):
                return " ".join((entry.findtext(path, "", NAMESPACES) or "").split())

            identifier = value("a:id")
            parsed = urlparse(identifier)
            if parsed.hostname not in (
                "arxiv.org",
                "export.arxiv.org",
            ) or not parsed.path.startswith("/abs/"):
                raise ValueError("unexpected entry id")
            arxiv_id = valid_id(parsed.path[5:])
            # 归一化到官方 HTTPS 地址；忽略 feed 中不受约束的下载链接。
            comment = value("arxiv:comment")
            abstract = value("a:summary")
            items.append(
                {
                    "arxiv_id": arxiv_id,
                    "base_id": re.sub(r"v\d+$", "", arxiv_id),
                    "title": value("a:title"),
                    "abstract": abstract,
                    "authors": [
                        " ".join((a.findtext("a:name", "", NAMESPACES)).split())
                        for a in entry.findall("a:author", NAMESPACES)
                    ],
                    "published": value("a:published"),
                    "updated": value("a:updated"),
                    "categories": [
                        c.attrib.get("term", "") for c in entry.findall("a:category", NAMESPACES)
                    ],
                    "comment": comment,
                    "doi": value("arxiv:doi"),
                    "journal": value("arxiv:journal_ref"),
                    "url": f"https://arxiv.org/abs/{arxiv_id}",
                    "pdf_url": f"https://arxiv.org/pdf/{arxiv_id}",
                    "code_urls": github_repositories(abstract + " " + comment),
                }
            )
        if total and not items:
            raise ValueError("empty nonzero feed")
        return {"total": total, "items": items}
    except (ET.ParseError, ValueError, TypeError) as exc:
        raise DiscoveryError("无法解析 arXiv 返回的论文列表，请稍后重试") from exc


class ArxivDiscovery:
    def __init__(self, store, transport=None, interval=3.1):
        self.store = store
        self.transport = transport
        self.lock = threading.Lock()
        self.last_request = 0
        self.interval = interval
        with store.connect() as db:
            db.executescript("""
                CREATE TABLE IF NOT EXISTS discovery_cache(key TEXT PRIMARY KEY,payload TEXT,fetched REAL);
                CREATE TABLE IF NOT EXISTS discovery_jobs(id TEXT PRIMARY KEY,arxiv_id TEXT,status TEXT,
                    document_id TEXT DEFAULT '',error TEXT DEFAULT '',created TEXT DEFAULT CURRENT_TIMESTAMP);
                CREATE TABLE IF NOT EXISTS document_sources(document_id TEXT,arxiv_id TEXT,metadata TEXT,
                    imported TEXT DEFAULT CURRENT_TIMESTAMP,PRIMARY KEY(document_id,arxiv_id));
            """)

    def client(self):
        return httpx.Client(
            transport=self.transport,
            timeout=httpx.Timeout(30, connect=10),
            follow_redirects=False,
            trust_env=False,
            headers={
                "User-Agent": "YanxiResearchAssistant/0.2 (arXiv metadata and user-selected PDF import)"
            },
        )

    def cached(self, key):
        with self.store.connect() as db:
            row = db.execute("SELECT * FROM discovery_cache WHERE key=?", (key,)).fetchone()
        return (json.loads(row["payload"]), row["fetched"]) if row else (None, 0)

    def request(self, params):
        key = json.dumps(params, sort_keys=True)
        with self.lock:
            cached, fetched = self.cached(key)
            if cached is not None and time.time() - fetched < CACHE_SECONDS:
                return {
                    **cached,
                    "cached": True,
                    "fetched_at": datetime.fromtimestamp(fetched, timezone.utc).isoformat(),
                }
            # 官方 API 建议请求间隔至少三秒；所有检索和编号查询共享节流。
            delay = self.interval - (time.monotonic() - self.last_request)
            if delay > 0:
                time.sleep(delay)
            self.last_request = time.monotonic()
            try:
                with (
                    self.client() as client,
                    client.stream("GET", API_URL, params=params) as response,
                ):
                    response.raise_for_status()
                    content = bytearray()
                    for part in response.iter_bytes():
                        content.extend(part)
                        if len(content) > 2 * 1024 * 1024:
                            raise DiscoveryError("arXiv 返回内容过大，请缩小检索范围")
                parsed = parse_feed(bytes(content))
                fetched = time.time()
                with self.store.connect() as db:
                    db.execute(
                        "INSERT OR REPLACE INTO discovery_cache VALUES(?,?,?)",
                        (key, json.dumps(parsed, ensure_ascii=False), fetched),
                    )
                    db.execute(
                        "DELETE FROM discovery_cache WHERE fetched<?", (fetched - 7 * 86400,)
                    )
                return {
                    **parsed,
                    "cached": False,
                    "fetched_at": datetime.fromtimestamp(fetched, timezone.utc).isoformat(),
                }
            except (httpx.HTTPError, DiscoveryError) as exc:
                if cached is not None:
                    return {
                        **cached,
                        "cached": True,
                        "stale": True,
                        "warning": "arXiv 暂时不可用，显示上次成功查询的缓存。",
                        "fetched_at": datetime.fromtimestamp(fetched, timezone.utc).isoformat(),
                    }
                if isinstance(exc, DiscoveryError):
                    raise
                raise DiscoveryError("arXiv 暂时不可用或请求超时，请稍后重试") from exc

    def search(self, query, start=0, sort="relevance"):
        params = {**query_expression(query), "start": start, "max_results": 8}
        if "search_query" in params:
            params.update(
                sortBy={"relevance": "relevance", "newest": "submittedDate"}[sort],
                sortOrder="descending",
            )
        return self.request(params)

    def lookup(self, arxiv_id):
        arxiv_id = valid_id(arxiv_id)
        result = self.request({"id_list": arxiv_id, "start": 0, "max_results": 1})
        if result.get("stale"):
            raise DiscoveryError("arXiv 暂时不可用，导入前需要重新核对论文版本，请稍后重试")
        for item in result["items"]:
            if item["arxiv_id"] == arxiv_id or (item["base_id"] == arxiv_id):
                return item
        raise DiscoveryError("未找到对应 arXiv 论文或版本", 404)

    def job(self, job_id):
        with self.store.connect() as db:
            row = db.execute("SELECT * FROM discovery_jobs WHERE id=?", (job_id,)).fetchone()
        return dict(row) if row else None

    def jobs(self):
        with self.store.connect() as db:
            return [
                dict(r)
                for r in db.execute(
                    "SELECT * FROM discovery_jobs ORDER BY created DESC,rowid DESC LIMIT 12"
                )
            ]

    def create_job(self, job_id, arxiv_id):
        with self.store.connect() as db:
            db.execute(
                "INSERT INTO discovery_jobs(id,arxiv_id,status) VALUES(?,?,'queued')",
                (job_id, arxiv_id),
            )

    def update_job(self, job_id, status, document_id="", error=""):
        with self.store.connect() as db:
            db.execute(
                "UPDATE discovery_jobs SET status=?,document_id=?,error=? WHERE id=?",
                (status, document_id, error, job_id),
            )

    def recover_jobs(self):
        with self.store.connect() as db:
            db.execute(
                "UPDATE discovery_jobs SET status='error',error='服务重启中断了下载，请重新导入' WHERE status IN ('queued','downloading')"
            )

    def source(self, document_id, item):
        with self.store.connect() as db:
            db.execute(
                "INSERT OR REPLACE INTO document_sources(document_id,arxiv_id,metadata) VALUES(?,?,?)",
                (document_id, item["arxiv_id"], json.dumps(item, ensure_ascii=False)),
            )

    def sources(self, document_id):
        with self.store.connect() as db:
            return [
                {**json.loads(r["metadata"]), "imported_at": r["imported"]}
                for r in db.execute(
                    "SELECT * FROM document_sources WHERE document_id=? ORDER BY imported DESC",
                    (document_id,),
                )
            ]

    def imported(self):
        with self.store.connect() as db:
            return {
                r["arxiv_id"]: {
                    "document_id": r["id"],
                    "status": r["status"],
                    "archived": bool(r["trashed_at"] or not r["is_active"]),
                }
                for r in db.execute(
                    "SELECT s.arxiv_id,d.* FROM document_sources s JOIN documents d ON d.id=s.document_id"
                )
            }

    def download(self, item):
        # 仅接受校验后的官方编号，按流读取且只允许官方 HTTPS 重定向。
        url = f"https://arxiv.org/pdf/{valid_id(item['arxiv_id'])}"
        started = time.monotonic()
        with self.client() as client:
            for _ in range(5):
                with client.stream("GET", url, timeout=60) as response:
                    if response.is_redirect:
                        target = response.url.join(response.headers.get("location", ""))
                        if target.scheme != "https" or target.host not in (
                            "arxiv.org",
                            "export.arxiv.org",
                            "www.arxiv.org",
                        ):
                            raise DiscoveryError("论文下载跳转到非 arXiv 地址，已停止")
                        url = str(target)
                        continue
                    response.raise_for_status()
                    if int(response.headers.get("content-length", "0")) > 100 * 1024 * 1024:
                        raise DiscoveryError("论文 PDF 超过 100 MB，请选择其他文献", 413)
                    data = bytearray()
                    for part in response.iter_bytes():
                        data.extend(part)
                        if len(data) > 100 * 1024 * 1024:
                            raise DiscoveryError("论文 PDF 超过 100 MB", 413)
                        if time.monotonic() - started > 120:
                            raise DiscoveryError("论文下载超时，请稍后重试", 504)
                    if b"%PDF-" not in data[:1024]:
                        raise DiscoveryError("arXiv 未返回有效 PDF，可能是访问限制，请稍后重试")
                    return bytes(data)
        raise DiscoveryError("论文下载重定向次数过多")
