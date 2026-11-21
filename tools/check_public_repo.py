# -*- coding: utf-8 -*-
"""Public repository hygiene check.

Verifies that no local databases, binaries, virtual environments, machine
paths, obvious secrets or oversized artifacts are committed. Intended for
local use and GitHub Actions. Does not inspect any user document library.
"""
from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
TEXT_EXT = {".py", ".md", ".txt", ".json", ".yml", ".yaml", ".ps1", ".mjs",
            ".iss", ".spec", ".qml", ".qss", ".html", ".js", ".css",
            ".svg", ".xml", ".manifest", ".gitignore", ".gitkeep"}
BINARY_EXT = {".png", ".ico", ".jpg", ".jpeg", ".gif", ".bmp"}
MAX_FILE = 5 * 1024 * 1024
FORBIDDEN_SUFFIX = {".db", ".db-wal", ".db-shm", ".db-journal", ".exe",
                    ".dll", ".pyd", ".pyc", ".pyo", ".so", ".dylib",
                    ".zip", ".7z", ".rar", ".xlsx", ".pack", ".obj", ".lib"}
FORBIDDEN_PARTS = {"__pycache__", ".venv", ".venv-qt", "build", "dist",
                   "backup", "ocr_cache", "web_downloads", "qa_history",
                   "维权包", "宣传素材"}
MACHINE_PATH = re.compile(
    r"[A-Za-z]:(\\\\|\\)(?:桌面|已完成的运营项目|Users\\|Users\\\\)", re.I)
SECRET_PATTERNS = [
    re.compile(r"sk-[A-Za-z0-9_\-]{16,}", re.I),
    re.compile(r"gh[pousr]_[A-Za-z0-9]{20,}"),
    re.compile(r"github_pat_[A-Za-z0-9_]{20,}"),
    re.compile(r"Bearer\s+[A-Za-z0-9._\-]{30,}", re.I),
    re.compile(r"1826592971|wxid_[A-Za-z0-9_]+|PZ4jgz|龙星华"),
    re.compile(r"All rights reserved|未经授权不得复制", re.I),
]


def tracked_files() -> list[Path]:
    r = subprocess.run(["git", "-C", str(ROOT), "ls-files", "-z"],
                       capture_output=True, check=True)
    return [ROOT / p for p in r.stdout.decode("utf-8").split("\0") if p]


def main() -> int:
    problems: list[str] = []
    files = tracked_files()
    for p in files:
        rel = p.relative_to(ROOT)
        if any(part in FORBIDDEN_PARTS for part in rel.parts[:-1]):
            problems.append(f"forbidden directory in path: {rel}")
        if rel.suffix.lower() in FORBIDDEN_SUFFIX:
            problems.append(f"forbidden artifact: {rel}")
        if not p.is_file():
            problems.append(f"missing tracked file: {rel}")
            continue
        if p.stat().st_size > MAX_FILE:
            problems.append(f"oversized file ({p.stat().st_size} bytes): {rel}")
        if rel.suffix.lower() in BINARY_EXT or p.suffix == "":
            continue
        if rel in (Path("tools") / "check_public_repo.py", Path("docs") / "MAINTENANCE_LOG.md"):
            # The checker contains literal examples of patterns it looks for;
            # the maintenance log cites a redacted historical token label.
            continue
        text = p.read_text(encoding="utf-8", errors="replace")
        for i, line in enumerate(text.splitlines(), 1):
            if MACHINE_PATH.search(line):
                problems.append(f"machine path at {rel}:{i}")
            for rx in SECRET_PATTERNS:
                if rx.search(line):
                    problems.append(f"secret-like / stale license text at {rel}:{i}")
                    break

    print(f"checked {len(files)} tracked files")
    if problems:
        for x in problems[:80]:
            print("FAIL " + x)
        if len(problems) > 80:
            print(f"... and {len(problems) - 80} more")
        return 1
    print("PASS: no forbidden artifacts, machine paths or secret-like strings")
    return 0


if __name__ == "__main__":
    sys.exit(main())
