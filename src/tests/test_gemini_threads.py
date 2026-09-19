"""tests/test_gemini_threads.py — GeminiFilterThread / GeminiDiagnosisThread run()
result and error signalling with gemini_helper patched out."""
import unittest
from unittest.mock import patch

from PyQt6.QtWidgets import QApplication

from threads.fetch_threads import GeminiFilterThread, GeminiDiagnosisThread

_qapp = QApplication.instance() or QApplication([])


class TestGeminiBackgroundThreads(unittest.TestCase):
    """Test asynchronous Gemini QThread execution."""

    @patch("gemini_helper.nl_to_filter")
    def test_gemini_filter_thread_success(self, mock_nl_to_filter):
        mock_nl_to_filter.return_value = {
            "text_filter": "Samsung",
            "conditions": [{"col": 7, "op": ">=", "val": 70000}],
            "explanation": "Price >= 70000",
        }
        thread = GeminiFilterThread("삼성전자 7만원 이상")
        results = []

        def _on_finished(res, err):
            results.append((res, err))

        thread.finished.connect(_on_finished)
        thread.run()

        self.assertEqual(len(results), 1)
        res, err = results[0]
        self.assertIsNotNone(res)
        self.assertEqual(err, "")
        self.assertEqual(res["text_filter"], "Samsung")

    @patch("gemini_helper.nl_to_filter", return_value=None)
    def test_gemini_filter_thread_none_result(self, mock_nl_to_filter):
        thread = GeminiFilterThread("invalid query")
        results = []

        def _on_finished(res, err):
            results.append((res, err))

        thread.finished.connect(_on_finished)
        thread.run()

        self.assertEqual(len(results), 1)
        res, err = results[0]
        self.assertIsNone(res)
        self.assertIn("failed", err)

    @patch("gemini_helper.portfolio_diagnosis")
    def test_gemini_diagnosis_thread_success(self, mock_diag):
        mock_diag.return_value = "Portfolio analysis report"
        thread = GeminiDiagnosisThread([], [])
        results = []

        def _on_finished(text, err):
            results.append((text, err))

        thread.finished.connect(_on_finished)
        thread.run()

        self.assertEqual(len(results), 1)
        text, err = results[0]
        self.assertEqual(text, "Portfolio analysis report")
        self.assertEqual(err, "")


if __name__ == "__main__":
    unittest.main()
