"""Verify that the pipeline CLI starts without making live requests."""

import subprocess
import sys
from pathlib import Path


def test_application_starts():
    root = Path(__file__).resolve().parents[1]
    result = subprocess.run(
        [sys.executable, str(root / "code" / "main.py"), "--help"],
        capture_output=True,
        text=True,
        check=False,
        timeout=10,
    )
    assert result.returncode == 0, result.stderr
    assert "--input" in result.stdout
    assert "--media-root" in result.stdout
    assert "--output" in result.stdout
    assert "--as-of-date" in result.stdout
    assert "--horizon-days" in result.stdout
