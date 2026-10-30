import importlib
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient

from backend.core import Store
from backend.test_structure import fixture
from backend.visual_structure import (
    VisualStructure,
    align_numeric_cells,
    check_region,
    table_row_crops,
    validated_items,
)


class VisualTests(unittest.TestCase):
    def test_numeric_position_correction_preserves_value_and_rejects_collision(self):
        from PIL import Image

        crop = Image.new("RGB", (500, 100))
        crop.info.update(
            column_centers=[0.1, 0.4, 0.6, 0.8, 0.9],
            row_blocks=[{"text": "39.2", "x": 0.6}, {"text": "1.0", "x": 0.9}],
        )
        row = ["Method", "39.2", "", "1.0 · 10^20", ""]
        self.assertEqual(align_numeric_cells(row, crop), ["Method", "", "39.2", "", "1.0 · 10^20"])
        crop.info["row_blocks"].append({"text": "39.2", "x": 0.4})
        self.assertEqual(align_numeric_cells(row, crop)[1], "39.2")
        crop.info["row_blocks"] = [{"text": "39.2", "x": 0.6}, {"text": "1.0", "x": 0.6}]
        with self.assertRaises(ValueError):
            align_numeric_cells(row, crop)

    def test_ocr_row_geometry_keeps_empty_columns_and_ambiguous_names_unmerged(self):
        from PIL import Image

        image = Image.new("RGB", (1000, 400), "white")
        page = {"method": "ocr", "width": 1000, "height": 400, "blocks": []}
        for text, x, y in [
            ("Model", 10, 20),
            ("Alpha", 10, 110),
            ("12", 700, 110),
            ("Alpha Ensemble", 10, 210),
            ("Beta", 10, 310),
        ]:
            page["blocks"].append(
                {
                    "text": text,
                    "bbox": [[x, y - 10], [x + 100, y - 10], [x + 100, y + 10], [x, y + 10]],
                }
            )
        rows = [
            ["Model", "Left", "Right"],
            ["Alpha", "", "12"],
            ["Alpha Ensemble", "13", ""],
            ["Beta", "14", "15"],
        ]
        crops = table_row_crops(image, page, [0, 0, 1, 1], rows)
        self.assertEqual(len(crops), 3)
        self.assertTrue(all(crop.info["single_table_row"] for crop in crops))
        self.assertTrue(all(crop.width == 1000 for crop in crops))
        self.assertEqual(
            len(
                table_row_crops(
                    image, {**page, "blocks": json.dumps(page["blocks"])}, [0, 0, 1, 1], rows
                )
            ),
            3,
        )
        self.assertEqual(
            table_row_crops(image, page, [0, 0, 1, 1], [rows[0], rows[3], rows[1]]), []
        )
        duplicate = {
            **page,
            "blocks": page["blocks"]
            + [{"text": "Beta", "bbox": [[10, 350], [110, 350], [110, 370], [10, 370]]}],
        }
        self.assertEqual(table_row_crops(image, duplicate, [0, 0, 1, 1], rows), [])

    def test_formula_preserves_latex_and_rejects_executable_commands(self):
        region = [0.1, 0.2, 0.8, 0.4]
        result = validated_items("```latex\n\\frac{x_1}{y^2} = 1\n```", "equation", region)[0]
        self.assertEqual(result["latex"], r"\frac{x_1}{y^2} = 1")
        self.assertEqual(result["bbox"], region)
        for value in ["", r"\input{password}", r"\write18{bad}", r"\include{other}"]:
            with self.assertRaises(ValueError):
                validated_items(value, "equation", region)

    def test_table_preserves_null_blanks_numbers_and_rejects_jagged_output(self):
        rows = [["Model", "Score", "Cost"], ["Alpha", "10^19", ""], ["Wide", None, "-2.50"]]
        item = validated_items(
            json.dumps({"caption": "Table 2", "rows": rows}), "table", [0, 0, 1, 1]
        )[0]
        self.assertEqual(item["rows"], rows)
        for rows in [[], [["one"]], [["a", "b"], ["c"]], [["a", "b"], [1, 2]], [["a", "b"]] * 81]:
            with self.assertRaises(ValueError):
                validated_items(json.dumps({"rows": rows}), "table", [0, 0, 1, 1])
        for region in [[1, 0, 0, 1], [0, 0, float("nan"), 1], [0, 0, 1], [0, 0, 0.001, 1]]:
            with self.assertRaises(ValueError):
                check_region(region)

    def test_real_render_cache_region_identity_and_source_change(self):
        with tempfile.TemporaryDirectory() as temp:
            store = Store(Path(temp))
            doc, _ = store.create(fixture(), "fixture.pdf")
            doc_id = doc["id"]
            store.begin(doc_id, 1)
            store.save_page(
                doc_id,
                1,
                {
                    "text": "Original page",
                    "method": "native",
                    "width": 612,
                    "height": 792,
                    "blocks": [],
                },
            )
            store.finish(doc_id, 1, [])
            model = VisualStructure()
            model.identity = "test-local-model"
            raw = json.dumps({"rows": [["Model", "Score"], ["Alpha", "10"]]})
            original = store.page(doc_id, 1)
            with patch.object(model, "infer", return_value=raw) as infer:
                first = model.extract(store, doc_id, 1, "table", [0.05, 0.05, 0.9, 0.4])
                self.assertFalse(first["cached"])
                self.assertEqual(first["document_id"], doc_id)
                size = infer.call_args[0][0].size
                self.assertGreater(size[0], 100)
                self.assertTrue(
                    model.extract(store, doc_id, 1, "table", [0.05, 0.05, 0.9, 0.4])["cached"]
                )
                self.assertEqual(infer.call_count, 1)
                model.extract(store, doc_id, 1, "table", [0.1, 0.1, 0.9, 0.5])
                self.assertEqual(infer.call_count, 2)
                self.assertEqual(store.page(doc_id, 1), original)
                model.lock.acquire()
                try:
                    with self.assertRaises(OverflowError):
                        model.extract(store, doc_id, 1, "table", [0.2, 0.1, 0.9, 0.5])
                finally:
                    model.lock.release()

                def changed(*args):
                    store.save_page(doc_id, 1, {**original, "text": "Changed original"})
                    return raw

                infer.side_effect = changed
                with self.assertRaises(ValueError):
                    model.extract(store, doc_id, 1, "table", [0.3, 0.1, 0.9, 0.5])
                store.trash(doc_id)
                with self.assertRaises(ValueError):
                    model.extract(store, doc_id, 1, "table", [0.05, 0.05, 0.9, 0.4])

    def test_api_validation_and_errors(self):
        module = importlib.import_module("backend.app")
        client = TestClient(module.app)
        url = "/api/documents/source/pages/1/visual-structure"
        with patch.object(
            module.visual_structure, "extract", return_value={"items": []}
        ) as extract:
            self.assertEqual(
                client.post(url, json={"kind": "table", "bbox": [0, 0, 1, 1]}).status_code, 200
            )
            extract.assert_called_once()
            for payload in [
                {"kind": "html", "bbox": [0, 0, 1, 1]},
                {"kind": "table", "bbox": [0, 0, 1]},
            ]:
                self.assertEqual(client.post(url, json=payload).status_code, 422)
            for error, status in [
                (ValueError("changed"), 409),
                (KeyError("missing"), 404),
                (OverflowError("busy"), 429),
                (RuntimeError("no model"), 503),
            ]:
                extract.side_effect = error
                self.assertEqual(
                    client.post(url, json={"kind": "table", "bbox": [0, 0, 1, 1]}).status_code,
                    status,
                )
