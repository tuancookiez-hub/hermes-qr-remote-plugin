# Repo-root conftest: puts the package root on sys.path so tests can
# `import sidecar` / `dashboard` regardless of how pytest is invoked
# (`python -m pytest` adds CWD itself; bare `pytest` does not).
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
