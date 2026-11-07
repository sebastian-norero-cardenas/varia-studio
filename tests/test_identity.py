"""Public identity and compatibility contract for Varia Studio."""

import subprocess
import sys
import unittest
from pathlib import Path

import csv_analyzer
from csv_analyzer import config as C

ROOT = Path(__file__).resolve().parents[1]


class IdentityTests(unittest.TestCase):
    def test_public_brand_and_version(self):
        self.assertEqual(C.APP_TITLE, "Varia Studio")
        self.assertEqual(csv_analyzer.__version__, "2.1.0")

        active_sources = (
            ROOT / "src/csv_analyzer/app.py",
            ROOT / "src/csv_analyzer/ui/main_window.py",
        )
        for path in active_sources:
            with self.subTest(path=path.name):
                text = path.read_text(encoding="utf-8")
                self.assertNotIn("CSV Analyzer", text)
                self.assertIn("C.APP_TITLE", text)

    def test_packaging_and_cli_compatibility_contract(self):
        text = (ROOT / "pyproject.toml").read_text(encoding="utf-8")
        self.assertIn('name = "csv-analyzer-desktop"', text)
        self.assertIn('version = "2.1.0"', text)
        self.assertIn(
            'description = "Varia Studio: a desktop workspace for exploring CSV and scientific DAT datasets"',
            text,
        )
        self.assertIn('varia-studio = "csv_analyzer.qt_app:main"', text)
        self.assertIn('csv-analyzer = "csv_analyzer.qt_app:main"', text)
        self.assertIn('varia-studio-qt = "csv_analyzer.qt_app:main"', text)
        self.assertEqual(text.count('= "csv_analyzer.qt_app:main"'), 3)
        self.assertNotIn('= "csv_analyzer.app:main"', text)

    def test_module_cli_help_keeps_dataset_argument(self):
        result = subprocess.run(
            [sys.executable, "-m", "csv_analyzer", "--help"],
            cwd=ROOT,
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("Varia Studio", result.stdout)
        self.assertIn("[-h]", result.stdout)
        self.assertIn("[--debug]", result.stdout)
        self.assertIn("[file]", result.stdout)
        self.assertIn("Dataset to open at startup", result.stdout)


if __name__ == "__main__":
    unittest.main()
