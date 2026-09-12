"""Verify that the application starts successfully."""

import subprocess
import sys
from pathlib import Path


def test_application_starts():
    root = Path(__file__).resolve().parents[1]
    result = subprocess.run(
        [sys.executable, str(root / "code" / "main.py")],
        capture_output=True,
        text=True,
        check=False,
        timeout=10,
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "Buy or Wait: ready (Milestone 1)."
