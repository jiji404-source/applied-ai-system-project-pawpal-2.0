"""Put the repo root on sys.path so tests import the project regardless of how
pytest is invoked (`pytest`, `python -m pytest`, or from a subdirectory)."""

import sys
from pathlib import Path

ROOT = str(Path(__file__).resolve().parent)
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)
