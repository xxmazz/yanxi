"""本机隔离 HTTPS 评审认证验收；一次性凭据，不启动模型、不触达公网。"""

import argparse
import hashlib
import json
import os
import re
import secrets
import shutil
import socket
import ssl
import subprocess
import sys
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from backend.access import password_hash  # noqa: E402


def sha(content):
    return hashlib.sha256(content).hexdigest()


def free_port():
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def source_hashes():
    names = [
        "backend/access.py",
        "backend/app.py",
        "src/api.ts",
        "scripts/verify_review_access.py",
        "backend/requirements.txt",
    ]
    return {name: sha((ROOT / name).read_bytes()) for name in names}


class Verification:
    def __init__(self, args):
        self.args = args
        self.processes = []
        self.clients = []
        self.logs = []
        self.report = {
            "verified_at": datetime.now(timezone.utc).isoformat(),
            "base_commit": subprocess.check_output(
                ["git", "rev-parse", "HEAD"], cwd=ROOT, text=True
            ).strip(),
            "source_sha256": source_hashes(),
            "git_working_tree_had_changes": bool(
                subprocess.check_output(["git", "status", "--porcelain"], cwd=ROOT)
            ),
            "checks": [],
            "services": [],
            "gpu_or_cloud_model_used": False,
            "production_server_or_library_used": False,
            "env_file_loaded": False,
            "limitations": [
                "真实回环HTTPS和HTTP进程、一次性自签证书及单份PDF的隔离验收，不等于公网HTTPS或反向代理部署验收。",
                "只为本次客户端显式信任临时localhost证书；未修改系统信任库，也未关闭证书或主机名验证。",
                "客户端通过HTTPX发送请求；未验证浏览器登录弹窗、缓存凭据、前端交互或视觉布局。",
                "只验证共享账号边界，不代表多用户数据隔离、专业渗透测试、限速或长期负载验收。",
                "所有模型环境配置清空，没有GPU/OCR/向量推理；已入库范围问答应明确503，而非生成答案。",
            ],
        }

    def check(self, name, condition, **details):
        self.report["checks"].append({"name": name, "passed": bool(condition), **details})
        if not condition:
            raise AssertionError(f"验收未通过：{name}")

    def client(self, **options):
        client = httpx.Client(trust_env=False, follow_redirects=False, timeout=30, **options)
        self.clients.append(client)
        return client

    def start(self, directory, label, port, env, certificate=None, key=None):
        command = [
            sys.executable,
            "-m",
            "uvicorn",
            "backend.app:app",
            "--host",
            "127.0.0.1",
            "--port",
            str(port),
            "--no-proxy-headers",
        ]
        if certificate:
            command.extend(["--ssl-certfile", str(certificate), "--ssl-keyfile", str(key)])
        log = (directory / f"{label}.log").open("w")
        self.logs.append(log)
        process = subprocess.Popen(command, cwd=ROOT, env=env, stdout=log, stderr=subprocess.STDOUT)
        entry = {"label": label, "port": port, "listen_host": "127.0.0.1", "pid": process.pid}
        self.report["services"].append(entry)
        self.processes.append((process, entry))
        started = time.monotonic()
        while time.monotonic() - started < 30:
            if process.poll() is not None:
                raise RuntimeError(f"{label}进程在启动时退出，退出码{process.returncode}")
            with socket.socket() as sock:
                sock.settimeout(0.2)
                if sock.connect_ex(("127.0.0.1", port)) == 0:
                    entry["startup_seconds"] = round(time.monotonic() - started, 3)
                    return
            time.sleep(0.1)
        raise RuntimeError(f"{label}进程启动超时")

    def stop(self):
        for client in self.clients:
            client.close()
        for process, entry in self.processes:
            if process.poll() is None:
                process.terminate()
                try:
                    process.wait(timeout=15)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait(timeout=5)
            entry["exit_code"] = process.returncode
            with socket.socket() as sock:
                entry["listener_stopped"] = sock.connect_ex(("127.0.0.1", entry["port"])) != 0
        for log in self.logs:
            log.close()

    def run(self, directory):
        args = self.args
        content = args.fixture_pdf.read_bytes()
        document_id = sha(content)
        self.report["fixture"] = {
            "filename": args.fixture_pdf.name,
            "sha256": document_id,
            "bytes": len(content),
        }
        if not (ROOT / "dist/index.html").is_file():
            raise ValueError("缺少dist/index.html，请先运行npm ci和npm run build")
        openssl = shutil.which("openssl")
        if not openssl:
            raise ValueError("未找到系统openssl")
        self.report["openssl"] = subprocess.check_output([openssl, "version"], text=True).strip()
        certificate, key, config = (
            directory / name for name in ["localhost.crt", "localhost.key", "openssl.cnf"]
        )
        config.write_text(
            "[req]\nprompt = no\ndistinguished_name = subject\nx509_extensions = server\n"
            "[subject]\nCN = localhost\n"
            "[server]\nbasicConstraints = critical,CA:FALSE\n"
            "keyUsage = critical,digitalSignature,keyEncipherment\nextendedKeyUsage = serverAuth\n"
            "subjectAltName = @names\n[names]\nDNS.1 = localhost\nIP.1 = 127.0.0.1\n"
        )
        generated = subprocess.run(
            [
                openssl,
                "req",
                "-x509",
                "-newkey",
                "rsa:2048",
                "-nodes",
                "-days",
                "1",
                "-config",
                str(config),
                "-keyout",
                str(key),
                "-out",
                str(certificate),
            ],
            capture_output=True,
            timeout=30,
        )
        if generated.returncode:
            raise RuntimeError("系统openssl未能生成一次性localhost证书")
        key.chmod(0o600)
        context = ssl.create_default_context(cafile=str(certificate))
        self.check(
            "tls_verification_enabled",
            context.check_hostname and context.verify_mode == ssl.CERT_REQUIRED,
            hostname_check=True,
            certificate_required=True,
            temporary_certificate_sha256=sha(certificate.read_bytes()),
        )
        password = secrets.token_urlsafe(32)
        username = "review-smoke"
        encoded = password_hash(password)
        tls_port = free_port()
        public_origin = f"https://localhost:{tls_port}"
        env = {
            key: value
            for key, value in os.environ.items()
            if not key.startswith("YANXI_") and key not in {"PYTHONPATH", "PYTHONHOME"}
        }
        env.update(
            CUDA_VISIBLE_DEVICES="",
            HF_HUB_OFFLINE="1",
            TRANSFORMERS_OFFLINE="1",
            YANXI_DATA_DIR=str(directory / "tls-data"),
            YANXI_DIST_DIR=str(ROOT / "dist"),
            YANXI_AUTH_USER=username,
            YANXI_AUTH_PASSWORD_HASH=encoded,
            YANXI_AUTH_ORIGIN=public_origin,
        )
        self.start(directory, "https", tls_port, env, certificate, key)
        anonymous = self.client(base_url=public_origin, verify=context)
        authorized = self.client(base_url=public_origin, verify=context, auth=(username, password))
        paths = [
            "/",
            "/api/health",
            "/api/documents",
            "/api/research/tasks",
            "/docs",
            "/redoc",
            "/openapi.json",
            "/assets/unknown.js",
            "/api/documents/private/pdf",
            "/api/documents/private/render/1",
            "/api/documents/private/translation/export",
            "/api/export/unknown",
            "/unknown/private-path",
        ]
        count = 0
        for path in paths:
            for method in ["GET", "HEAD", "POST"]:
                result = anonymous.request(method, path)
                self.check(
                    "anonymous_route_denied",
                    result.status_code == 401
                    and "Basic" in result.headers.get("www-authenticate", "")
                    and result.headers.get("cache-control") == "no-store",
                    method=method,
                    path=path,
                    status=result.status_code,
                )
                count += 1
        self.report["anonymous_requests_denied"] = count
        print(f"已验证{count}个未授权路径/方法组合", flush=True)
        homepage = authorized.get("/")
        self.check(
            "authorized_homepage",
            homepage.status_code == 200 and "<title>" in homepage.text,
            status=homepage.status_code,
        )
        for path in re.findall(r'(?:src|href)="(/assets/[^" ]+\.(?:js|css))"', homepage.text):
            result = authorized.get(path)
            self.check(
                "authorized_static_asset",
                result.status_code == 200,
                path=path,
                status=result.status_code,
            )
        health = authorized.get("/api/health")
        self.check(
            "authorized_cpu_health",
            health.status_code == 200
            and not any(
                health.json()[key] for key in ["model_ready", "embedding_ready", "ocr_ready"]
            ),
            status=health.status_code,
            health=health.json(),
        )
        result = anonymous.get("/api/health", auth=(username, "deliberately-wrong-password"))
        self.check("wrong_password_denied", result.status_code == 401, status=result.status_code)
        for path in ["/", "/api/health", "/docs", "/redoc", "/openapi.json"]:
            result = authorized.get(path)
            self.check(
                "private_response_headers",
                result.status_code == 200
                and result.headers.get("cache-control") == "no-store"
                and "Authorization" in result.headers.get("vary", "")
                and result.headers.get("x-frame-options") == "DENY",
                path=path,
                status=result.status_code,
            )
        self.check(
            "isolated_library_initially_empty", authorized.get("/api/documents").json() == []
        )
        files = {"file": (args.fixture_pdf.name, content, "application/pdf")}
        marker = {"X-Yanxi-Request": "1"}
        for label, headers in [
            ("missing_marker", {}),
            ("wrong_origin", {**marker, "Origin": "https://attacker.example"}),
            ("cross_site", {**marker, "Origin": public_origin, "Sec-Fetch-Site": "cross-site"}),
            ("same_site", {**marker, "Origin": public_origin, "Sec-Fetch-Site": "same-site"}),
        ]:
            result = authorized.post("/api/documents", files=files, headers=headers)
            empty = authorized.get("/api/documents").json() == []
            self.check(
                "unsafe_upload_refused",
                result.status_code == 403 and empty,
                case=label,
                status=result.status_code,
                library_still_empty=empty,
            )
        result = authorized.get("/api/discovery/search", params={"q": "attention"})
        self.check(
            "search_without_marker_refused_before_network",
            result.status_code == 403,
            status=result.status_code,
        )
        result = authorized.get(
            "/api/health",
            headers={
                "Sec-Fetch-Site": "cross-site",
                "Sec-Fetch-Mode": "no-cors",
                "Sec-Fetch-Dest": "image",
            },
        )
        self.check(
            "cross_site_subresource_refused", result.status_code == 403, status=result.status_code
        )
        good_headers = {**marker, "Origin": public_origin, "Sec-Fetch-Site": "same-origin"}
        result = authorized.post("/api/documents", files=files, headers=good_headers)
        self.check(
            "same_origin_marked_upload",
            result.status_code == 202 and result.json()["id"] == document_id,
            status=result.status_code,
            document_id=document_id,
        )
        started = time.monotonic()
        while time.monotonic() - started < args.parse_timeout:
            documents = authorized.get("/api/documents").raise_for_status().json()
            document = next((item for item in documents if item["id"] == document_id), None)
            if document and document["status"] not in {"queued", "processing"}:
                break
            time.sleep(0.2)
        self.check(
            "uploaded_document_ready",
            document is not None and document["status"] == "ready",
            status=document["status"] if document else "missing",
            physical_pages=document["pages"] if document else 0,
            parse_seconds=round(time.monotonic() - started, 3),
        )
        pdf_path = f"/api/documents/{document_id}/pdf"
        for path in [
            pdf_path,
            f"/api/documents/{document_id}/render/1",
            f"/api/documents/{document_id}/translation/export",
        ]:
            result = anonymous.get(path)
            self.check(
                "real_document_unauthenticated_denied",
                result.status_code == 401,
                path=path,
                status=result.status_code,
            )
        result = authorized.get(pdf_path)
        self.check(
            "authorized_pdf_identity",
            result.status_code == 200 and sha(result.content) == document_id,
            status=result.status_code,
            sha256=sha(result.content),
            bytes=len(result.content),
        )
        result = authorized.get(pdf_path, headers={"Range": "bytes=0-31"})
        self.check(
            "authorized_pdf_byte_range",
            result.status_code == 206
            and result.content == content[:32]
            and result.headers.get("content-range") == f"bytes 0-31/{len(content)}",
            status=result.status_code,
            content_range=result.headers.get("content-range"),
        )
        result = authorized.head(pdf_path)
        self.check(
            "authorized_pdf_head",
            result.status_code == 200
            and not result.content
            and int(result.headers.get("content-length", "-1")) == len(content),
            status=result.status_code,
            body_bytes=len(result.content),
            content_length=result.headers.get("content-length"),
        )
        result = authorized.post(
            "/api/graph/build", headers=good_headers, json={"document_ids": [document_id]}
        )
        graph = result.json()
        self.check(
            "authorized_graph_build",
            result.status_code == 200
            and graph["stats"]["documents"] == 1
            and graph["stats"]["works"] == 1,
            status=result.status_code,
            stats=graph.get("stats"),
            fingerprint=graph.get("fingerprint"),
        )
        result = authorized.post(
            "/api/chat",
            headers=good_headers,
            json={"question": "请解释这篇论文的方法和主要结论。", "document_ids": [document_id]},
        )
        self.check(
            "cpu_question_explicit_model_unavailable",
            result.status_code == 503 and "模型未就绪" in result.json().get("detail", ""),
            status=result.status_code,
            generated_answer=False,
        )
        if shutil.which("pdftoppm"):
            result = authorized.get(f"/api/documents/{document_id}/render/1")
            self.check(
                "authorized_original_page_png",
                result.status_code == 200 and result.content.startswith(b"\x89PNG\r\n\x1a\n"),
                status=result.status_code,
                bytes=len(result.content),
            )
        else:
            self.report["optional_render_png"] = (
                "未运行：缺少系统Poppler；原PDF身份、Range和HEAD已单独验证。"
            )
        print("正确认证下的上传、解析、PDF和无模型边界已验证", flush=True)
        plain_port = free_port()
        plain_env = {
            **env,
            "YANXI_DATA_DIR": str(directory / "plain-data"),
            "YANXI_AUTH_ORIGIN": "https://review.example",
        }
        self.start(directory, "http-rejection", plain_port, plain_env)
        plain = self.client(base_url=f"http://127.0.0.1:{plain_port}")
        for label, auth, headers in [
            ("anonymous", None, {"Host": "review.example"}),
            ("correct_credentials", (username, password), {"Host": "review.example"}),
            (
                "spoofed_forwarded_proto",
                (username, password),
                {"Host": "review.example", "X-Forwarded-Proto": "https"},
            ),
        ]:
            result = plain.get("/api/health", auth=auth, headers=headers)
            self.check(
                "plain_http_cannot_reach_https_configuration",
                result.status_code == 426 and "www-authenticate" not in result.headers,
                case=label,
                status=result.status_code,
                no_basic_challenge="www-authenticate" not in result.headers,
            )
        self.check(
            "source_stable_during_verification", self.report["source_sha256"] == source_hashes()
        )


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--fixture-pdf", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--parse-timeout", type=int, default=180)
    args = parser.parse_args()
    args.fixture_pdf = args.fixture_pdf.expanduser().resolve()
    if not args.fixture_pdf.is_file() or not 1 <= args.parse_timeout <= 600:
        parser.error("请提供现存PDF及1—600秒的解析等待预算")
    args.output.parent.mkdir(parents=True, exist_ok=True)
    run = Verification(args)
    temporary_path = None
    with tempfile.TemporaryDirectory(prefix="yanxi-review-access-") as temporary:
        temporary_path = Path(temporary)
        try:
            run.run(temporary_path)
            run.report["all_checks_passed"] = True
        except Exception as exc:
            # 不序列化请求对象、进程环境、凭据或临时服务日志。
            run.report["all_checks_passed"] = False
            run.report["error"] = {"type": type(exc).__name__, "message": str(exc)[:400]}
        finally:
            run.stop()
    run.report["temporary_credentials_and_certificate_files_removed"] = not temporary_path.exists()
    run.report["all_temporary_listeners_stopped"] = all(
        item.get("listener_stopped") for item in run.report["services"]
    )
    run.report["all_checks_passed"] = (
        run.report["all_checks_passed"]
        and run.report["temporary_credentials_and_certificate_files_removed"]
        and run.report["all_temporary_listeners_stopped"]
    )
    run.report["finished_at"] = datetime.now(timezone.utc).isoformat()
    args.output.write_text(json.dumps(run.report, ensure_ascii=False, indent=2) + "\n")
    print(
        json.dumps(
            {
                "report": str(args.output),
                "all_checks_passed": run.report["all_checks_passed"],
                "checks": len(run.report["checks"]),
                "temporary_listeners_stopped": run.report["all_temporary_listeners_stopped"],
                "error": run.report.get("error"),
            },
            ensure_ascii=False,
        )
    )
    return 0 if run.report["all_checks_passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
