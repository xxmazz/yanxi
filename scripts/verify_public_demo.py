"""对显式指定的 HTTPS 评审入口做只读验收；不部署、不上传、不执行模型推理。"""

import argparse
import getpass
import hashlib
import ipaddress
import json
import os
import re
import ssl
import sys
import time
from datetime import datetime, timezone
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import urljoin, urlsplit

import httpx

MAX_PDF = 100 * 1024 * 1024
MAX_RESOURCE = 20 * 1024 * 1024


def sha(value):
    return hashlib.sha256(value).hexdigest()


def public_origin(value):
    """只接受 HTTPS 源，不让账号、查询或重定向变成认证发送目的地。"""
    try:
        parsed = urlsplit(value)
        port = parsed.port
        if (
            parsed.scheme != "https"
            or not parsed.hostname
            or parsed.username is not None
            or parsed.password is not None
            or parsed.path not in {"", "/"}
            or parsed.query
            or parsed.fragment
            or port == 0
            or re.search(r"[\s\\\x00-\x1f\x7f]", value)
        ):
            raise ValueError
        host = parsed.hostname.lower().encode("idna").decode("ascii")
        if host == "localhost" or host.endswith((".localhost", ".local", ".invalid")):
            raise ValueError
        try:
            address = ipaddress.ip_address(host)
        except ValueError:
            address = None
        if address is not None and not address.is_global:
            raise ValueError
        if address is not None and address.version == 6:
            host = f"[{host}]"
        if port not in {None, 443}:
            host += f":{port}"
        return f"https://{host}"
    except (ValueError, UnicodeError) as exc:
        raise ValueError("入口须为不带账号、路径、查询的公网 HTTPS 源") from exc


class Assets(HTMLParser):
    def __init__(self):
        super().__init__()
        self.links = []

    def handle_starttag(self, tag, attributes):
        fields = dict(attributes)
        if tag == "script" and fields.get("src"):
            self.links.append(fields["src"])
        if tag == "link" and "stylesheet" in fields.get("rel", "").split():
            self.links.append(fields.get("href", ""))


def asset_path(base, value):
    parsed = urlsplit(urljoin(base + "/", value))
    if (
        f"{parsed.scheme}://{parsed.netloc}" != base
        or not parsed.path.startswith("/assets/")
        or parsed.query
        or parsed.fragment
        or re.search(r"[\s\\\x00-\x1f\x7f]", value)
    ):
        raise ValueError("首页资源须位于同源 /assets/ 路径")
    return parsed.path


class Verification:
    def __init__(self, base, document_id, username, password, page=1, transport=None):
        self.base = public_origin(base)
        if not re.fullmatch(r"[0-9a-f]{64}", document_id):
            raise ValueError("document-id须为原始PDF的64位小写SHA-256")
        if not re.fullmatch(r"[A-Za-z0-9_.-]{1,64}", username) or not 12 <= len(password) <= 256:
            raise ValueError("请提供有效评审账号及12至256字符的密码")
        if not 1 <= page <= 650:
            raise ValueError("物理页码须为1至650")
        self.document_id, self.page = document_id, page
        context = ssl.create_default_context()
        self.anonymous = httpx.Client(
            base_url=self.base,
            verify=context,
            trust_env=False,
            follow_redirects=False,
            timeout=httpx.Timeout(60, connect=15),
            transport=transport,
        )
        self.authorized = httpx.Client(
            base_url=self.base,
            verify=context,
            trust_env=False,
            follow_redirects=False,
            timeout=httpx.Timeout(60, connect=15),
            transport=transport,
            auth=httpx.BasicAuth(username, password),
        )
        self.report = {
            "verified_at": datetime.now(timezone.utc).isoformat(),
            "base_url": self.base,
            "document_sha256": document_id,
            "script_sha256": sha(Path(__file__).read_bytes()),
            "tls_certificate_and_hostname_verification_configured": (
                context.check_hostname and context.verify_mode == ssl.CERT_REQUIRED
            ),
            "redirects_followed": False,
            "simulation_transport_used": transport is not None,
            "business_data_mutations_requested": False,
            "model_inference_requested": False,
            "independent_external_network_verified": False,
            "checks": [],
            "limitations": [
                "须从实际外网机器运行；系统证书校验通过不证明长期可用或所有网络均可达。",
                "入口仅做HTTPS源格式及显式本地IP过滤，未用DNS证明解析地址公网可达。",
                "只读HTTPX验收，不证明浏览器登录、前端点击、下载落盘或真实模型回答。",
                "health只核对模型ready，不证明GPU设备映射、推理质量或并发能力。",
                "原页GET可能生成预览缓存；不调用上传、重建、修改、翻译、问答或构图接口。",
                "引用只抽样一处正文、一条书目与一个目录原文区间，不表示完整引用召回率。",
            ],
        }

    def close(self):
        self.authorized.close()
        self.anonymous.close()

    def check(self, name, condition, **details):
        self.report["checks"].append({"name": name, "passed": bool(condition), **details})
        if not condition:
            raise AssertionError(name)

    def read(self, path, status=200, *, anonymous=False, method="GET", headers=None, limit=None):
        """不返回重定向正文；上限和时间预算避免误下载无界资源。"""
        resolved = urlsplit(urljoin(self.base + "/", path))
        if (
            method not in {"GET", "HEAD"}
            or not path.startswith("/")
            or path.startswith("//")
            or f"{resolved.scheme}://{resolved.netloc}" != self.base
            or resolved.query
            or resolved.fragment
            or re.search(r"[\s\\\x00-\x1f\x7f]", path)
        ):
            raise ValueError("只允许同源、不带查询的GET/HEAD路径")
        client = self.anonymous if anonymous else self.authorized
        started = time.monotonic()
        with client.stream(method, path, headers=headers) as response:
            self.check(
                "http_status",
                response.status_code == status,
                path=path,
                status=response.status_code,
            )
            content = bytearray()
            for piece in response.iter_bytes():
                if (
                    len(content) + len(piece) > (limit or MAX_RESOURCE)
                    or time.monotonic() - started > 180
                ):
                    raise ValueError("response_size_or_time_limit")
                content.extend(piece)
            return dict(response.headers), bytes(content)

    def read_json(self, path):
        headers, content = self.read(path)
        self.check(
            "json_media_type", "application/json" in headers.get("content-type", ""), path=path
        )
        return json.loads(content)

    def run(self):
        prefix = f"/api/documents/{self.document_id}"
        for path in [
            "/",
            "/api/health",
            "/api/documents",
            prefix + "/pdf",
            prefix + "/reading-links",
            prefix + f"/render/{self.page}",
        ]:
            headers, _ = self.read(path, 401, anonymous=True)
            self.check(
                "anonymous_denied",
                "Basic" in headers.get("www-authenticate", "")
                and headers.get("cache-control") == "no-store",
                path=path,
            )

        headers, home = self.read("/")
        self.check(
            "authorized_home",
            "text/html" in headers.get("content-type", "")
            and headers.get("cache-control") == "no-store",
        )
        parser = Assets()
        parser.feed(home.decode("utf-8"))
        paths = sorted({asset_path(self.base, value) for value in parser.links})
        self.check(
            "frontend_asset_list",
            1 <= len(paths) <= 8
            and any(p.endswith(".js") for p in paths)
            and any(p.endswith(".css") for p in paths),
        )
        assets = []
        for path in paths:
            self.read(path, 401, anonymous=True)
            headers, body = self.read(path)
            expected_type = "javascript" if path.endswith(".js") else "text/css"
            self.check(
                "frontend_asset",
                expected_type in headers.get("content-type", "") and len(body) > 0,
                path=path,
            )
            assets.append({"path": path, "bytes": len(body), "sha256": sha(body)})
        self.report["frontend_assets"] = assets

        health = self.read_json("/api/health")
        self.check(
            "three_local_models_ready",
            isinstance(health, dict)
            and health.get("status") == "ok"
            and all(
                health.get(key) is True for key in ["model_ready", "embedding_ready", "ocr_ready"]
            ),
        )
        documents = self.read_json("/api/documents")
        self.check("document_list", isinstance(documents, list))
        selected = next(
            (
                doc
                for doc in documents
                if isinstance(doc, dict) and doc.get("id") == self.document_id
            ),
            {},
        )
        self.check(
            "selected_pdf_ready_and_active",
            selected.get("status") == "ready"
            and selected.get("is_active") == 1
            and selected.get("trashed_at") is None
            and isinstance(selected.get("pages"), int)
            and selected["pages"] >= self.page,
        )

        headers, body = self.read(prefix + "/pdf", method="HEAD")
        length = int(headers.get("content-length", "0"))
        self.check(
            "pdf_head",
            "application/pdf" in headers.get("content-type", "")
            and 0 < length <= MAX_PDF
            and not body,
        )
        headers, part = self.read(
            prefix + "/pdf", 206, headers={"Range": "bytes=0-1023"}, limit=1024
        )
        last = min(1023, length - 1)
        self.check(
            "pdf_range",
            headers.get("content-range") == f"bytes 0-{last}/{length}"
            and len(part) == last + 1
            and b"%PDF-" in part,
        )
        headers, whole = self.read(prefix + "/pdf", limit=MAX_PDF)
        self.check(
            "original_pdf_hash",
            len(whole) == length and sha(whole) == self.document_id,
            bytes=len(whole),
        )
        del whole

        headers, image = self.read(prefix + f"/render/{self.page}")
        self.check(
            "original_page_png_signature",
            "image/png" in headers.get("content-type", "")
            and image.startswith(b"\x89PNG\r\n\x1a\n"),
            physical_page=self.page,
            bytes=len(image),
        )
        links = self.read_json(prefix + "/reading-links")
        self.check(
            "reading_navigation",
            isinstance(links, dict)
            and links.get("document_id") == self.document_id
            and links.get("offset_encoding") == "unicode-code-points"
            and isinstance(links.get("fingerprint"), str)
            and all(
                isinstance(links.get(key), list) and links[key]
                for key in ["entries", "mentions", "outline"]
            ),
        )
        samples = [links["mentions"][0], links["entries"][0]["evidence"][0], links["outline"][0]]
        pages, checked = {}, []
        for item in samples:
            page = item["page"]
            self.check(
                "source_scope",
                item.get("document_id") == self.document_id
                and isinstance(page, int)
                and 1 <= page <= selected["pages"],
            )
            if page not in pages:
                pages[page] = self.read_json(prefix + f"/pages/{page}")["text"]
            text = pages[page]
            start, end, quote = item["start"], item["end"], item["quote"]
            valid = (
                isinstance(start, int)
                and isinstance(end, int)
                and 0 <= start < end <= len(text)
                and text[start:end] == quote
                and sha(text.encode()) == item["page_sha256"]
            )
            self.check("sampled_source_interval", valid, physical_page=page)
            checked.append(
                {
                    "physical_page": page,
                    "start": start,
                    "end": end,
                    "page_sha256": item["page_sha256"],
                    "quote_sha256": sha(quote.encode()),
                }
            )
        self.report["sampled_source_intervals"] = checked
        self.report["all_requested_checks_passed"] = True
        return self.report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-url", required=True, help="显式HTTPS源，不带路径或凭据")
    parser.add_argument(
        "--document-id", required=True, help="已就绪PDF的SHA-256，建议选原生Transformer"
    )
    parser.add_argument("--page", type=int, default=1)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    verification = None
    try:
        public_origin(args.base_url)
        if not sys.stdin.isatty() and not all(
            os.environ.get(key) for key in ["YANXI_VERIFY_USER", "YANXI_VERIFY_PASSWORD"]
        ):
            raise ValueError("非交互运行须从私有环境提供两项凭据，拒绝回显口令")
        username = os.environ.get("YANXI_VERIFY_USER") or input("评审账号：").strip()
        password = os.environ.get("YANXI_VERIFY_PASSWORD") or getpass.getpass(
            "评审密码（不回显）："
        )
        verification = Verification(args.base_url, args.document_id, username, password, args.page)
        verification.run()
        success = True
    except Exception as exc:
        # 不记录异常正文或服务端body，避免认证头／回显凭据进入日志。
        success = False
        if verification is not None:
            verification.report["all_requested_checks_passed"] = False
            verification.report["error_type"] = type(exc).__name__
        print(f"验收未通过（{type(exc).__name__}），检查配置及本地报告。", file=sys.stderr)
    finally:
        if verification is not None:
            verification.close()
            args.output.parent.mkdir(parents=True, exist_ok=True)
            args.output.write_text(
                json.dumps(verification.report, ensure_ascii=False, indent=2) + "\n"
            )
    if success:
        print(f"只读HTTPS验收通过：{len(verification.report['checks'])}项检查。")
    return 0 if success else 1


if __name__ == "__main__":
    raise SystemExit(main())
