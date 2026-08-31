"""tests/test_ui_common.py — Unit tests for shared UI common helpers and atomic I/O.
"""

import os
import tempfile
import unittest
from PyQt6.QtWidgets import QApplication
from PyQt6.QtGui import QFont

# Ensure a QApplication instance exists before creating QFont widgets
app = QApplication.instance() or QApplication([])

from ui.common import (
    create_font,
    _validate_date_str,
    _validate_positive_number,
    atomic_save_json,
    safe_load_json,
)


class TestUiCommonValidators(unittest.TestCase):
    def test_validate_date_str(self):
        self.assertTrue(_validate_date_str("2026-08-31"))
        self.assertTrue(_validate_date_str("  2025-01-01  "))
        self.assertFalse(_validate_date_str("2026-02-30"))  # invalid leap/day
        self.assertFalse(_validate_date_str("2026/08/31"))  # wrong separator
        self.assertFalse(_validate_date_str(""))
        self.assertFalse(_validate_date_str("not-a-date"))

    def test_validate_positive_number(self):
        self.assertTrue(_validate_positive_number("100"))
        self.assertTrue(_validate_positive_number("1,234,567.89"))
        self.assertTrue(_validate_positive_number("15.5%"))
        self.assertTrue(_validate_positive_number("  0.001  "))
        self.assertFalse(_validate_positive_number("0"))
        self.assertFalse(_validate_positive_number("-15.5"))
        self.assertFalse(_validate_positive_number(""))
        self.assertFalse(_validate_positive_number("abc"))


class TestAtomicJsonIO(unittest.TestCase):
    def setUp(self):
        self.test_dir = tempfile.mkdtemp()

    def tearDown(self):
        import shutil
        if os.path.exists(self.test_dir):
            shutil.rmtree(self.test_dir, ignore_errors=True)

    def test_atomic_save_and_safe_load(self):
        file_path = os.path.join(self.test_dir, "test_data.json")
        payload = {"ticker": "005930", "name": "Samsung", "items": [1, 2, 3]}

        atomic_save_json(file_path, payload)
        self.assertTrue(os.path.exists(file_path))

        loaded = safe_load_json(file_path)
        self.assertEqual(loaded, payload)

    def test_safe_load_nonexistent_returns_default(self):
        file_path = os.path.join(self.test_dir, "nonexistent.json")
        loaded_empty = safe_load_json(file_path)
        self.assertEqual(loaded_empty, {})

        loaded_custom = safe_load_json(file_path, default=[])
        self.assertEqual(loaded_custom, [])

    def test_safe_load_corrupted_returns_default(self):
        file_path = os.path.join(self.test_dir, "corrupted.json")
        with open(file_path, "w", encoding="utf-8") as f:
            f.write("{invalid json content---")

        loaded = safe_load_json(file_path, default={"fallback": True})
        self.assertEqual(loaded, {"fallback": True})


class TestCreateFont(unittest.TestCase):
    def test_create_font_defaults(self):
        font = create_font(size=12)
        self.assertIsInstance(font, QFont)
        self.assertEqual(font.pointSize(), 12)

    def test_create_font_bold(self):
        font = create_font(size=14, weight=QFont.Weight.Bold)
        self.assertIsInstance(font, QFont)
        self.assertEqual(font.pointSize(), 14)
        self.assertEqual(font.weight(), QFont.Weight.Bold)


if __name__ == "__main__":
    unittest.main()
