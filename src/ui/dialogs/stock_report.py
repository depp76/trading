"""ui/dialogs/stock_report.py — Gemini per-stock AI report popup (roadmap 2-1,
review.md 2-1)."""
from PyQt6.QtWidgets import QDialog, QVBoxLayout, QHBoxLayout, QLabel, QPushButton, QTextEdit
from PyQt6.QtGui import QFont

from ui.common import create_font


def show_stock_report_result(parent, ticker: str, name: str, result_text: str):
    """Display the AI per-stock report in a modal dialog."""
    result_dlg = QDialog(parent)
    result_dlg.setWindowTitle(f"🤖 AI Stock Report — {name} ({ticker})")
    result_dlg.resize(480, 320)
    v = QVBoxLayout(result_dlg)
    v.setContentsMargins(16, 14, 16, 14)
    v.setSpacing(10)

    title_lbl = QLabel(f"📊 {name} ({ticker})")
    title_lbl.setFont(create_font(12, QFont.Weight.Bold))
    title_lbl.setStyleSheet("color:#0a3d62; margin-bottom:4px;")
    v.addWidget(title_lbl)

    text_edit = QTextEdit()
    text_edit.setReadOnly(True)
    text_edit.setFont(create_font(10, style_name="Semilight"))
    text_edit.setStyleSheet(
        "QTextEdit { border:1px solid #d0d0d0; border-radius:6px; padding:8px; background:#fafafa; }"
    )
    import re
    html_text = re.sub(r"\*\*(.+?)\*\*", r"<b>\1</b>", result_text)
    html_text = html_text.replace("\n", "<br>")
    text_edit.setHtml(html_text)
    v.addWidget(text_edit, 1)

    disclaimer_lbl = QLabel("※ This analysis is for reference only and does not constitute investment advice.")
    disclaimer_lbl.setFont(create_font(8, style_name="Semilight"))
    disclaimer_lbl.setStyleSheet("color:#888;")
    v.addWidget(disclaimer_lbl)

    close_btn = QPushButton("Close")
    close_btn.setFixedWidth(80)
    close_btn.clicked.connect(result_dlg.accept)
    btn_row = QHBoxLayout()
    btn_row.addStretch()
    btn_row.addWidget(close_btn)
    v.addLayout(btn_row)

    result_dlg.exec()
