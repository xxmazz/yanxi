"""迁移、版本隔离和扫描件路由的行为回归；不需要 GPU 权重。"""

import logging
import sqlite3
import tempfile
import threading
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

from backend.core import Store, chunks_for_page
from backend.parsing import extract_page, metadata, native_text, native_title, page_payload
from backend.similarity import compare_documents, content_profile, identity


class DocumentTests(unittest.TestCase):
    def test_native_title_keeps_short_prefix_and_ignores_large_diagram_labels(self):
        def extract(visitor_text):
            for size, x, y, part in [
                (17, 80, 700, "Nova 2"),
                (17, 145, 700, ": Open Research Models"),
                (24, 100, 250, "Pretrained Weights"),
                (20, 30, 230, "arXiv:2307.12345"),
            ]:
                visitor_text(part, [1, 0, 0, 1, 0, 0], [1, 0, 0, 1, x, y], {}, size)

        page = SimpleNamespace(mediabox=SimpleNamespace(height=792), extract_text=extract)
        self.assertEqual(native_title(page), "Nova 2: Open Research Models")

    def test_native_title_joins_small_caps_and_continued_word(self):
        def extract(visitor_text):
            for size, x, y, part in [
                (17, 80, 700, "A"),
                (13.6, 92, 700, "STER: L"),
                (13.6, 180, 700, " OW-RANK LAN-"),
                (13.6, 80, 680, "GUAGE MODELS"),
            ]:
                visitor_text(part, [1, 0, 0, 1, 0, 0], [1, 0, 0, 1, x, y], {}, size)

        page = SimpleNamespace(mediabox=SimpleNamespace(height=792), extract_text=extract)
        self.assertEqual(native_title(page), "ASTER: LOW-RANK LANGUAGE MODELS")

    def test_parser_limit_warning_survives_page_storage(self):
        def extract():
            logging.getLogger("pypdf._page").warning(
                "Exceeded 5000 form XObject invocations while extracting text; further form content is skipped."
            )
            return "A native page with enough actual body text to avoid OCR routing."

        page = SimpleNamespace(
            extract_text=extract, images=[], mediabox=SimpleNamespace(width=100, height=200)
        )
        parsed = extract_page(page, Path("complex.pdf"), 19, Mock())
        self.assertIn("部分内容可能缺失", parsed["warning"])
        with tempfile.TemporaryDirectory() as directory:
            store = Store(Path(directory))
            doc, _ = store.create(b"%PDF-warning", "complex.pdf")
            store.save_page(doc["id"], 19, parsed)
            self.assertEqual(store.page(doc["id"], 19)["warning"], parsed["warning"])

    def test_parser_warning_collector_is_thread_scoped_and_removed_after_errors(self):
        logger = logging.getLogger("pypdf")
        original = list(logger.handlers)

        def extract():
            other = threading.Thread(
                target=lambda: logging.getLogger("pypdf._page").warning("other document")
            )
            other.start()
            other.join()
            return "current page"

        self.assertEqual(native_text(SimpleNamespace(extract_text=extract)), ("current page", ""))
        with self.assertRaises(ValueError):
            native_text(SimpleNamespace(extract_text=Mock(side_effect=ValueError("bad page"))))
        self.assertEqual(logger.handlers, original)

    def test_migration_keeps_old_library_and_citations(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            with sqlite3.connect(root / "library.sqlite3") as db:
                db.executescript("""
                  CREATE TABLE documents(id TEXT PRIMARY KEY,filename TEXT,title TEXT,pages INTEGER,status TEXT,error TEXT,created TEXT);
                  CREATE TABLE chunks(id INTEGER PRIMARY KEY,document_id TEXT,page INTEGER,offset INTEGER,text TEXT,vector BLOB);
                  INSERT INTO documents VALUES('old','old.pdf','Original',9,'ready','','2026-10-26');
                  INSERT INTO chunks VALUES(1,'old',7,0,'original evidence',NULL);
                """)
            store = Store(root)
            self.assertEqual(store.documents()[0]["title"], "Original")
            self.assertEqual(store.rows([])[0]["page"], 7)
            self.assertNotIn("content_vector", store.document("old"))
            Store(root)  # 第二次启动迁移幂等
            self.assertEqual(len(store.rows([])), 1)

    def test_content_match_ignores_filename_and_recommends_newer(self):
        words = " ".join(f"word{i}" for i in range(500))
        incoming = {
            "title": None,
            "profile": content_profile(words + " revised passage"),
            "arxiv_id": "1706.03762",
            "version": 1,
        }
        existing = {
            "id": "v7",
            "title": "arbitrary name",
            "profile": content_profile(words),
            "arxiv_id": "1706.03762",
            "version": 7,
        }
        result = compare_documents(incoming, existing)
        self.assertGreater(result["overlap"], 0.95)
        self.assertEqual(result["recommendation"], "keep_existing")
        self.assertEqual(identity("arXiv:1706.03762v7 [cs.CL]"), ("1706.03762", 7))
        self.assertIsNone(
            compare_documents(
                {
                    **incoming,
                    "profile": content_profile(" ".join(f"different{i}" for i in range(500))),
                },
                existing,
            )
        )

    def test_same_title_or_arxiv_identifier_alone_does_not_match(self):
        a = {
            "title": "Same title",
            "profile": content_profile(" ".join(f"aa{i}" for i in range(500))),
            "arxiv_id": "1",
            "version": 1,
        }
        b = {**a, "id": "other", "profile": content_profile(" ".join(f"bb{i}" for i in range(500)))}
        self.assertIsNone(compare_documents(a, b))

    def test_decisions_archive_and_restore_preserve_original_sources(self):
        for action in ("keep_both", "keep_existing", "replace_existing"):
            with self.subTest(action=action), tempfile.TemporaryDirectory() as directory:
                store = Store(Path(directory))
                old, _ = store.create(b"%PDF-old", "old.pdf")
                new, _ = store.create(b"%PDF-new", "new.pdf")
                chunks = [{"page": 3, "offset": 0, "text": "Original source evidence"}]
                store.finish(old["id"], 3, chunks)
                store.finish(new["id"], 3, chunks, analysis={"matches": [{"id": old["id"]}]})
                self.assertEqual(len(store.rows([])), 1)
                store.resolve(new["id"], old["id"], action)
                self.assertEqual(len(store.rows([])), 2 if action == "keep_both" else 1)
                expected = old if action == "keep_existing" else new
                self.assertIn(expected["id"], {r["document_id"] for r in store.rows([])})
                with self.assertRaises(ValueError):
                    store.resolve(new["id"], old["id"], action)
                store.trash(expected["id"])
                self.assertEqual(store.rows([expected["id"]]), [])
                self.assertTrue((store.root / (old["id"] + ".pdf")).exists())
                self.assertTrue((store.root / (new["id"] + ".pdf")).exists())
                store.trash(expected["id"], restore=True)
                self.assertEqual(len(store.rows([expected["id"]])), 1)
                with store.connect() as db:
                    self.assertEqual(
                        db.execute("SELECT count(*) FROM version_decisions").fetchone()[0], 1
                    )

    def test_multiple_conflicts_require_individual_decisions(self):
        with tempfile.TemporaryDirectory() as directory:
            store = Store(Path(directory))
            docs = [store.create(f"%PDF-{i}".encode(), f"{i}.pdf")[0] for i in range(3)]
            chunks = list(chunks_for_page("source text", 1))
            for doc in docs[:2]:
                store.finish(doc["id"], 1, chunks)
            store.finish(
                docs[2]["id"], 1, chunks, analysis={"matches": [{"id": d["id"]} for d in docs[:2]]}
            )
            result = store.resolve(docs[2]["id"], docs[0]["id"], "keep_both")
            self.assertEqual(result["status"], "review")
            self.assertEqual(len(store.rows([])), 2)
            result = store.resolve(docs[2]["id"], docs[1]["id"], "keep_both")
            self.assertEqual(result["status"], "ready")
            self.assertEqual(len(store.rows([])), 3)

    def test_page_storage_and_scan_native_blank_routes(self):
        ocr = Mock()
        ocr.page.return_value = {
            "text": "OCR evidence",
            "method": "ocr",
            "width": 200,
            "height": 300,
            "blocks": [{"text": "OCR evidence", "bbox": [[1, 2]], "confidence": 0.9}],
        }
        page = SimpleNamespace(
            extract_text=lambda: "",
            images=[object()],
            mediabox=SimpleNamespace(width=100, height=150),
        )
        result = extract_page(page, Path("scan.pdf"), 7, ocr)
        ocr.page.assert_called_once_with(Path("scan.pdf"), 7)
        with tempfile.TemporaryDirectory() as directory:
            store = Store(Path(directory))
            doc, _ = store.create(b"%PDF-test", "scan.pdf")
            store.begin(doc["id"], 7)
            store.save_page(doc["id"], 7, result)
            saved = page_payload(store.page(doc["id"], 7))
            self.assertEqual(saved["blocks"][0]["confidence"], 0.9)
            self.assertEqual(store.document(doc["id"])["ocr_pages"], 1)
        page.images = []
        self.assertEqual(extract_page(page, Path("blank.pdf"), 1, ocr)["method"], "native")
        page.extract_text = lambda: (
            "This is an ordinary native PDF text layer with many actual words and symbols."
        )
        page.images = [object()]
        self.assertEqual(extract_page(page, Path("native.pdf"), 1, ocr)["method"], "native")
        self.assertEqual(ocr.page.call_count, 1)

    def test_reindexing_an_existing_document_does_not_hide_version_candidate(self):
        with tempfile.TemporaryDirectory() as directory:
            store = Store(Path(directory))
            text = " ".join(f"term{i}" for i in range(300))
            chunks = list(chunks_for_page(text, 1))
            old, _ = store.create(b"%PDF-v7", "latest.pdf")
            info = {"title": "Original paper", "arxiv_id": "1706.03762", "version": 7}
            analysis = store.analyze(old["id"], text, chunks, info)
            store.finish(old["id"], 1, chunks, info["title"], analysis, info)
            store.queue(old["id"])
            new, _ = store.create(b"%PDF-v1", "random.pdf")
            analysis = store.analyze(new["id"], text, chunks, {**info, "version": 1})
            self.assertEqual(analysis["matches"][0]["id"], old["id"])
            self.assertEqual(analysis["matches"][0]["recommendation"], "keep_existing")

    def test_scan_title_uses_visible_header_instead_of_export_filename(self):
        reader = SimpleNamespace(metadata={"/Title": "unrelated-export-name"})
        page = {
            "method": "ocr",
            "text": "Attention Is All You Need\nAshish Vaswani\nGoogle Brain\nAbstract\nActual abstract",
            "height": 1000,
            "blocks": [
                {
                    "text": "Attention Is All You Need",
                    "bbox": [[100, 40], [800, 40], [800, 80], [100, 80]],
                },
                {
                    "text": "Ashish Vaswani",
                    "bbox": [[100, 100], [300, 100], [300, 120], [100, 120]],
                },
                {
                    "text": "A very large diagram caption",
                    "bbox": [[100, 700], [800, 700], [800, 800], [100, 800]],
                },
            ],
        }
        info = metadata(reader, page)
        self.assertEqual(info["title"], "Attention Is All You Need")
        self.assertEqual(info["authors"], "Ashish Vaswani")

    def test_large_profile_sketch_bound(self):
        profile = content_profile(" ".join(f"token{i}" for i in range(20000)))
        self.assertEqual(len(profile["sketch"]), 4096)
        self.assertEqual(
            compare_documents(
                {"profile": profile, "title": ""}, {"id": "same", "title": "", "profile": profile}
            )["overlap"],
            1,
        )


if __name__ == "__main__":
    unittest.main()
