"""Puts src/ on sys.path once for every test module, so tests can import the
application packages (data, strategy, ui, threads, ...) regardless of the
directory pytest is launched from or how deep the test file is nested."""
import os
import sys

_SRC_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _SRC_DIR not in sys.path:
    sys.path.insert(0, _SRC_DIR)
