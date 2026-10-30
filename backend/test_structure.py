import csv
import importlib
import io
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient
from pypdf import PdfWriter
from pypdf.generic import DecodedStreamObject, DictionaryObject, NameObject

from backend.core import Store
from backend.structure import analyze, extract, scientific_word, table_csv


def fixture():
    writer = PdfWriter()
    page = writer.add_blank_page(612, 792)
    font = DictionaryObject(
        {
            NameObject("/Type"): NameObject("/Font"),
            NameObject("/Subtype"): NameObject("/Type1"),
            NameObject("/BaseFont"): NameObject("/Helvetica"),
        }
    )
    page[NameObject("/Resources")] = DictionaryObject(
        {NameObject("/Font"): DictionaryObject({NameObject("/F1"): writer._add_object(font)})}
    )
    ops = []

    def text(x, y, value):
        value = value.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")
        ops.append(f"BT /F1 10 Tf {x} {y} Td ({value}) Tj ET")

    text(50, 750, "Table 1: Model comparison")
    for y in (730, 710, 650):
        ops.append(f"50 {y} m 500 {y} l S")
    for y, values in zip(
        (717, 697, 677, 657),
        [
            ("Model", "Score", "Cost"),
            ("Alpha", "10", "20"),
            ("Beta", "15", "25"),
            ("Gamma", "30", "40"),
        ],
    ):
        for x, value in zip((60, 270, 400), values):
            text(x, y, value)
    text(180, 450, "f(x) = x + 1")
    text(550, 450, "(1)")
    text(80, 400, "A body paragraph with a (2) parenthesis is not a formula.")
    # A diagram without a table caption must not be exported as tabular data.
    for y in (200, 180, 160):
        ops.append(f"50 {y} m 500 {y} l S")
    stream = DecodedStreamObject()
    stream.set_data("\n".join(ops).encode())
    page[NameObject("/Contents")] = writer._add_object(stream)
    out = io.BytesIO()
    writer.write(out)
    return out.getvalue()


class StructureTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.path = self.root / "sample.pdf"
        self.path.write_bytes(fixture())

    def tearDown(self):
        self.temp.cleanup()

    def store(self):
        store = Store(self.root / "library")
        doc, _ = store.create(fixture(), "sample.pdf")
        store.begin(doc["id"], 1)
        store.save_page(
            doc["id"],
            1,
            {"text": "fixture", "method": "native", "width": 612, "height": 792, "blocks": []},
        )
        store.finish(doc["id"], 1, [{"page": 1, "offset": 0, "text": "fixture"}])
        return store, doc["id"]

    def test_real_pdf_table_formula_and_diagram_exclusion(self):
        data = extract(self.path, 1)
        self.assertEqual([i["kind"] for i in data["items"]], ["table", "equation"])
        table, equation = data["items"]
        self.assertEqual(
            table["rows"],
            [
                ["Model", "Score", "Cost"],
                ["Alpha", "10", "20"],
                ["Beta", "15", "25"],
                ["Gamma", "30", "40"],
            ],
        )
        self.assertEqual(equation["label"], "(1)")
        self.assertIn("f(x) = x + 1", equation["text"])
        for item in data["items"]:
            x0, y0, x1, y1 = item["bbox"]
            self.assertTrue(0 <= x0 < x1 <= 1 and 0 <= y0 < y1 <= 1)

    def test_cache_timeout_and_scan_do_not_modify_library(self):
        store, doc_id = self.store()
        original = store.page(doc_id, 1)
        data = analyze(store, doc_id, 1)
        with patch("backend.structure.subprocess.run", side_effect=AssertionError("cached")):
            self.assertEqual(analyze(store, doc_id, 1), data)
        self.assertEqual(store.page(doc_id, 1), original)
        cache = self.root / "library" / "structure-v2" / doc_id / "1.json"
        cache.unlink()
        with patch(
            "backend.structure.subprocess.run", side_effect=subprocess.TimeoutExpired("pdf", 45)
        ):
            with self.assertRaises(ValueError):
                analyze(store, doc_id, 1)
        self.assertFalse(cache.exists())
        store.save_page(doc_id, 1, {**original, "method": "ocr", "blocks": []})
        self.assertEqual(analyze(store, doc_id, 1)["items"], [])
        with self.assertRaises(KeyError):
            analyze(store, doc_id, 0)

    def test_csv_preserves_quotes_null_negative_and_blocks_spreadsheet_execution(self):
        data = table_csv(
            {"rows": [["a,b", "line\nnext", None], ["-1.5", '=HYPERLINK("x")', " @SUM(1)"]]}
        )
        rows = list(csv.reader(io.StringIO(data.lstrip("\ufeff"))))
        self.assertEqual(rows[0], ["a,b", "line\nnext", ""])
        self.assertEqual(rows[1], ["-1.5", '\'=HYPERLINK("x")', "' @SUM(1)"])

    def test_scientific_notation_needs_actual_raised_digit_geometry(self):
        chars = [{"text": c, "size": 10, "bottom": 100} for c in "2.3·10"] + [
            {"text": c, "size": 7, "bottom": 96} for c in "19"
        ]
        self.assertEqual(scientific_word({"text": "2.3·1019", "chars": chars}), "2.3·10^19")
        for char in chars:
            char["size"] = 10
        self.assertEqual(scientific_word({"text": "2.3·1019", "chars": chars}), "2.3·1019")

    def test_api_real_analysis_image_and_csv(self):
        module = importlib.import_module("backend.app")
        store, doc_id = self.store()
        client = TestClient(module.app)
        with patch.object(module, "store", store):
            base = f"/api/documents/{doc_id}/pages/1/structure"
            result = client.get(base)
            self.assertEqual(result.status_code, 200)
            self.assertEqual(len(result.json()["items"]), 2)
            csv_response = client.get(base + "/0/csv")
            self.assertEqual(csv_response.status_code, 200)
            self.assertIn("Alpha,10,20", csv_response.text)
            self.assertIn("attachment", csv_response.headers["content-disposition"])
            # Use the actual local Poppler executable, not a mocked image.
            image = client.get(base + "/1/image")
            self.assertEqual(image.status_code, 200)
            self.assertTrue(image.content.startswith(b"\x89PNG"))
            self.assertEqual(client.get(base + "/1/csv").status_code, 404)
            self.assertEqual(client.get(base + "/-1/image").status_code, 404)
            self.assertEqual(client.get(base.replace("/pages/1/", "/pages/2/")).status_code, 404)
