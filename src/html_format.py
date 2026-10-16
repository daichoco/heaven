"""Shared HTML formatting for generated reports and the docs formatter."""

import shutil
import subprocess
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parents[1]
FORMAT_SCRIPT = ROOT_DIR / "scripts" / "format-html.cjs"


def format_html(source: str) -> str:
    node = shutil.which("node")
    if node is None:
        raise RuntimeError("HTML formatting requires Node.js on PATH; install Node.js and run npm ci.")
    result = subprocess.run(
        [node, str(FORMAT_SCRIPT), "--stdin"],
        input=source,
        encoding="utf-8",
        capture_output=True,
        check=False,
        cwd=ROOT_DIR,
    )
    if result.returncode:
        raise RuntimeError(f"HTML formatting failed (run npm ci):\n{result.stderr}")
    return result.stdout


def write_html(path: Path, source: str) -> None:
    formatted = format_html(source)
    path.write_text(formatted, encoding="utf-8", newline="\n")
