"""F4.D canonical frontend, metadata and lazy-import contracts."""

from __future__ import annotations

import importlib.metadata
import runpy
import subprocess
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

from csv_analyzer.qt_app.bootstrap import build_parser

ROOT = Path(__file__).resolve().parents[1]


class CanonicalQtCutoverTests(unittest.TestCase):
    def test_installed_distribution_metadata_routes_all_public_scripts_to_qt(self):
        distribution = importlib.metadata.distribution("csv-analyzer-desktop")
        scripts = {
            entry.name: entry.value
            for entry in distribution.entry_points
            if entry.group == "console_scripts"
        }
        expected = {
            "varia-studio": "csv_analyzer.qt_app:main",
            "csv-analyzer": "csv_analyzer.qt_app:main",
            "varia-studio-qt": "csv_analyzer.qt_app:main",
        }
        self.assertEqual({name: scripts.get(name) for name in expected}, expected)

    def test_module_main_routes_to_qt_main(self):
        with patch("csv_analyzer.qt_app.main", return_value=37) as qt_main:
            with self.assertRaises(SystemExit) as exit_context:
                runpy.run_module("csv_analyzer", run_name="__main__")
        self.assertEqual(exit_context.exception.code, 37)
        qt_main.assert_called_once_with()

    def test_import_csv_analyzer_is_gui_lazy(self):
        code = (
            "import sys; import csv_analyzer; "
            "assert 'PySide6.QtWidgets' not in sys.modules; "
            "assert 'customtkinter' not in sys.modules; "
            "print(csv_analyzer.__version__)"
        )
        result = subprocess.run(
            [sys.executable, "-c", code],
            cwd=ROOT,
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout.strip(), "2.1.0")

    def test_canonical_parser_preserves_file_and_debug_contract(self):
        parser = build_parser()
        args = parser.parse_args(["sample.csv", "--debug"])
        self.assertEqual(args.file, Path("sample.csv"))
        self.assertTrue(args.debug)

        args = parser.parse_args([])
        self.assertIsNone(args.file)
        self.assertFalse(args.debug)


if __name__ == "__main__":
    unittest.main()
