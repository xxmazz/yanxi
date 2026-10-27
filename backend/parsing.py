"""本地文本层解析及逐页 OCR，保留物理页码与 OCR 坐标。"""

import json
import logging
import os
import re
import shutil
import subprocess
import tempfile
import threading
from pathlib import Path
from statistics import median

from .similarity import identity


class LocalOCR:
    def __init__(self):
        self.reader = None
        self.lock = threading.Lock()
        self.path = os.getenv("YANXI_OCR_PATH", "")

    def load(self):
        if not self.path:
            return
        if not shutil.which("pdftoppm"):
            raise RuntimeError("OCR 需要 Poppler pdftoppm")
        import easyocr

        self.reader = easyocr.Reader(
            ["ch_sim", "en"],
            gpu=os.getenv("YANXI_OCR_DEVICE", "cuda:3"),
            model_storage_directory=self.path,
            download_enabled=False,
            verbose=False,
        )

        self.english = easyocr.Reader(
            ["en"],
            gpu=os.getenv("YANXI_OCR_DEVICE", "cuda:3"),
            model_storage_directory=self.path,
            download_enabled=False,
            detector=False,
            verbose=False,
        )
        # EasyOCR 默认 DataParallel 会选择 cuda:0；服务中 OCR 独占指定卡，
        # 去掉该包装，避免访问已经运行问答模型的 GPU 0/1。
        import torch

        for reader in (self.reader, self.english):
            for name in ("detector", "recognizer"):
                network = getattr(reader, name, None)
                if isinstance(network, torch.nn.DataParallel):
                    setattr(reader, name, network.module)

    def page(self, path, number):
        if self.reader is None:
            raise ValueError("此页缺少文本层，需要启用本地 OCR 后重新解析")
        import numpy as np
        from PIL import Image

        with self.lock, tempfile.TemporaryDirectory(prefix="yanxi-ocr-") as directory:
            prefix = str(Path(directory) / "page")
            subprocess.run(
                [
                    "pdftoppm",
                    "-f",
                    str(number),
                    "-l",
                    str(number),
                    "-scale-to",
                    "2200",
                    "-png",
                    "-singlefile",
                    str(path),
                    prefix,
                ],
                check=True,
                capture_output=True,
                timeout=120,
            )
            with Image.open(prefix + ".png") as image:
                width, height = image.size
                array = np.asarray(image.convert("RGB"))
                horizontal, free = self.reader.detect(array)
                results = self.reader.recognize(
                    array,
                    horizontal[0],
                    free[0],
                    detail=1,
                    paragraph=False,
                    batch_size=8,
                    workers=0,
                )
                sample = "".join(text for _, text, _ in results)
                # 中英联合识别器在英文论文中可能把拉丁字母误作汉字；以正文语言
                # 分流到英文识别头，同一次检测得到的区域保持一致。
                chinese = len(re.findall(r"[\u4e00-\u9fff]", sample))
                if chinese / max(1, len(sample)) < 0.03:
                    results = self.english.recognize(
                        array,
                        horizontal[0],
                        free[0],
                        detail=1,
                        paragraph=False,
                        batch_size=8,
                        workers=0,
                    )
        blocks = [
            {
                "bbox": [[float(x), float(y)] for x, y in box],
                "text": text,
                "confidence": round(float(confidence), 4),
            }
            for box, text, confidence in results
            if text.strip()
        ]
        # 两栏正文先按列、再按纵向排列；跨栏标题独立置顶。
        narrow = [
            b
            for b in blocks
            if max(p[0] for p in b["bbox"]) - min(p[0] for p in b["bbox"]) < width * 0.55
        ]
        left = sum(max(p[0] for p in b["bbox"]) < width * 0.56 for b in narrow)
        right = sum(min(p[0] for p in b["bbox"]) > width * 0.42 for b in narrow)
        crossing = sum(
            min(p[0] for p in b["bbox"]) < width * 0.46
            and max(p[0] for p in b["bbox"]) > width * 0.54
            for b in blocks
        )
        columns = left >= 6 and right >= 6 and crossing < max(5, len(blocks) * 0.15)
        row_height = (
            median([max(p[1] for p in b["bbox"]) - min(p[1] for p in b["bbox"]) for b in blocks])
            if blocks
            else 1
        )

        def order(block):
            xs, ys = [p[0] for p in block["bbox"]], [p[1] for p in block["bbox"]]
            if not columns:
                return (0, min(ys), min(xs))
            wide = max(xs) - min(xs) >= width * 0.55
            return (
                -1 if wide and min(ys) < height * 0.3 else 0 if min(xs) < width * 0.42 else 1,
                min(ys),
                min(xs),
            )

        blocks.sort(key=order)
        # 同一行的词框纵向坐标略有差异，按行聚合后再从左到右，避免词序颠倒。
        ordered, row, baseline, group = [], [], 0, None
        for block in blocks:
            key = order(block)
            if row and (key[0] != group or abs(key[1] - baseline) > row_height * 0.5):
                ordered.extend(sorted(row, key=lambda b: min(p[0] for p in b["bbox"])))
                row = []
            if not row:
                baseline, group = key[1], key[0]
            row.append(block)
        ordered.extend(sorted(row, key=lambda b: min(p[0] for p in b["bbox"])))
        blocks = ordered
        text = "\n".join(b["text"] for b in blocks)
        if len(text.strip()) < 25:
            raise ValueError(f"第 {number} 页 OCR 未识别到足够正文，请核对扫描质量")
        return {"text": text, "method": "ocr", "width": width, "height": height, "blocks": blocks}


class ExtractionWarnings(logging.Handler):
    """记录当前解析线程的页级警告，不关闭 PDF 库的资源保护上限。"""

    def __init__(self):
        super().__init__(logging.WARNING)
        self.owner = threading.get_ident()
        self.messages = []

    def emit(self, record):
        if record.thread != self.owner:
            return
        raw = record.getMessage()
        if "further form content is skipped" in raw:
            message = "本页图形对象超出解析保护上限，部分内容可能缺失，请核对原页。"
        elif "fontTools is required" in raw:
            message = "数学字体解析依赖不完整，部分符号可能有误，请核对原页。"
        else:
            message = "PDF 文本提取提示：" + " ".join(raw.split())[:200]
        if message not in self.messages and len(self.messages) < 3:
            self.messages.append(message)


def native_text(page):
    collector = ExtractionWarnings()
    logger = logging.getLogger("pypdf")
    logger.addHandler(collector)
    try:
        text = page.extract_text() or ""
    finally:
        logger.removeHandler(collector)
    return text, "；".join(collector.messages)


def extract_page(page, path, number, ocr):
    text, warning = native_text(page)
    # 有图像且正文稀少时 OCR；保留纯空白页，不把页码水印当正文。
    if len(re.sub(r"\W", "", text)) < 40 and len(page.images):
        parsed = ocr.page(path, number)
        if warning:
            parsed["warning"] = warning + " 已改用本地 OCR，请结合原页核对识别结果。"
        return parsed
    return {
        "text": text,
        "method": "native",
        "width": float(page.mediabox.width),
        "height": float(page.mediabox.height),
        "blocks": [],
        "warning": warning,
    }


def metadata(reader, first_page):
    info = reader.metadata or {}
    text = first_page["text"]
    title = str(info.get("/Title", "")).strip()
    if (
        first_page["method"] == "ocr"
        or not title
        or not re.search(r"\s", title)
        or len(title) < 8
        or re.search(r"\.pdf$|untitled|microsoft word", title, re.I)
    ):
        spans = []

        def visit(part, cm, tm, font, size):
            clean = " ".join(part.split())
            if 8 < len(clean) < 220 and not re.search(
                r"arxiv|provided proper attribution", clean, re.I
            ):
                spans.append((float(size), clean))

        if first_page["method"] == "native":
            reader.pages[0].extract_text(visitor_text=visit)
            top = max((s for s, _ in spans), default=0)
            title = " ".join(t for s, t in spans if s >= top * 0.95 and s >= 12)
        else:
            blocks = [
                b
                for b in first_page["blocks"]
                if min(y for _, y in b["bbox"]) < first_page["height"] * 0.28
            ]
            sizes = [
                (max(y for _, y in b["bbox"]) - min(y for _, y in b["bbox"]), b) for b in blocks
            ]
            top = max((size for size, b in sizes if 8 < len(b["text"]) < 180), default=0)
            title = " ".join(
                b["text"]
                for size, b in sizes
                if size >= top * 0.85 and top > 0 and 8 < len(b["text"]) < 180
            )[:300]
    authors = str(info.get("/Author", "")).strip()
    if not authors:
        # 仅从摘要前的署名区域抽取，机构行及邮箱不混入人名。
        front = re.split(r"\babstract\b|摘要", text, maxsplit=1, flags=re.I)[0]
        lines = [line.strip() for line in front.splitlines()]
        names = []
        for line in lines:
            clean = re.sub(r"[∗*†‡\d\s,，]+$", "", line).strip()
            if (
                2 <= len(clean.split()) <= 5
                and len(clean) < 80
                and not re.search(
                    r"@|arxiv|google|university|institute|research|brain|department|school|laborator|学院|大学",
                    clean,
                    re.I,
                )
                and clean not in title
                and not re.search(r"[:/\d]", clean)
                and all(word[:1].isupper() for word in clean.split())
            ):
                names.append(clean)
        authors = "; ".join(dict.fromkeys(names))[:500]
    arxiv_id, version = identity(text)
    abstract = re.search(
        r"(?:\babstract\b|摘要)\s*[:：]?\s*(.+?)(?:\n\s*(?:1[.\s]+)?introduction\b|\n\s*关键词|$)",
        text,
        re.I | re.S,
    )
    return {
        "title": title[:300] or None,
        "authors": authors[:500],
        "abstract": " ".join(re.split(r"\n\s*[∗†‡]", abstract.group(1), maxsplit=1)[0].split())[
            :1800
        ]
        if abstract
        else "",
        "arxiv_id": arxiv_id,
        "version": version,
    }


def page_payload(row):
    result = dict(row)
    result["blocks"] = json.loads(result["blocks"])
    return result
