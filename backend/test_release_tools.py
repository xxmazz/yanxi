import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from scripts.verify_release import run


class ReleaseGuardTests(unittest.TestCase):
    def test_runner_rejects_production_nonlocal_and_unconfirmed_destinations_before_io(self):
        with (
            tempfile.TemporaryDirectory() as folder,
            patch("scripts.verify_release.httpx.Client") as client,
        ):
            for url, isolated in [
                ("http://127.0.0.1:18478", False),
                ("http://127.0.0.1:18474", True),
                ("http://192.168.3.193:18478", True),
                ("http://user:secret@localhost:18478", True),
                ("http://localhost:18478/wrong", True),
                ("http://localhost:18478?target=other", True),
            ]:
                args = SimpleNamespace(
                    base_url=url, isolated_library=isolated, output_dir=Path(folder) / "reports"
                )
                with self.subTest(url=url, isolated=isolated), self.assertRaises(ValueError):
                    run(args)
                self.assertFalse(args.output_dir.exists())
            client.assert_not_called()

    def test_existing_report_is_preserved_without_contacting_service(self):
        with (
            tempfile.TemporaryDirectory() as folder,
            patch("scripts.verify_release.httpx.Client") as client,
        ):
            target = Path(folder)
            record = target / "previous.json"
            record.write_text('{"passed":false}')
            args = SimpleNamespace(
                base_url="http://127.0.0.1:18478", isolated_library=True, output_dir=target
            )
            with self.assertRaises(ValueError):
                run(args)
            self.assertEqual(record.read_text(), '{"passed":false}')
            client.assert_not_called()


if __name__ == "__main__":
    unittest.main()
