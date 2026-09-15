"""run.py - convenience launcher for the repo root.

Runs src/main.py as if invoked directly (`python src/main.py`), so the app
can be started with `python run.py` without cd'ing into src/. Must stay at
the repo root so relative paths to portfolio.db, .env, and the cache/state
JSON files (all resolved against the current working directory) still work.
"""
import os
import runpy

_SRC_MAIN = os.path.join(os.path.dirname(os.path.abspath(__file__)), "src", "main.py")

if __name__ == "__main__":
    runpy.run_path(_SRC_MAIN, run_name="__main__")
