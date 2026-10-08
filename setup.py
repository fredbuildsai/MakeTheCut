#!/usr/bin/env python3
"""Creates or updates the virtual environment only when needed."""

import hashlib
import subprocess
import sys
import shutil
from pathlib import Path

BASE = Path(__file__).parent
VENV = BASE / ".venv"
REQ  = BASE / "requirements.txt"
STAMP = VENV / ".req_hash"


def run(cmd: list, **kwargs):
    print(f"  $ {' '.join(str(c) for c in cmd)}", flush=True)
    subprocess.run(cmd, check=True, **kwargs)


def _req_hash() -> str:
    return hashlib.md5(REQ.read_bytes()).hexdigest()


def _venv_ok() -> bool:
    """Return True if the venv exists and was built from the current requirements."""
    python = VENV / "bin" / "python"
    if not python.exists():
        return False
    if not STAMP.exists():
        return False
    return STAMP.read_text().strip() == _req_hash()


def main():
    print("─" * 50, flush=True)

    if _venv_ok():
        print("✅  Virtual environment is up to date — skipping setup.", flush=True)
        print("─" * 50, flush=True)
        return

    if VENV.exists():
        print("🗑  Requirements changed — removing existing .venv...", flush=True)
        shutil.rmtree(VENV)

    print("🐍  Creating fresh virtual environment...", flush=True)
    run([sys.executable, "-m", "venv", str(VENV)])

    pip = VENV / "bin" / "pip"
    print("📦  Installing dependencies...", flush=True)
    run([str(pip), "install", "-r", str(REQ)])

    print("⚡  Pre-compiling bytecode…", flush=True)
    run([str(VENV / "bin" / "python"), "-m", "compileall", "-q", str(VENV / "lib")])

    STAMP.write_text(_req_hash())
    print("✅  Setup complete.", flush=True)
    print("─" * 50, flush=True)


if __name__ == "__main__":
    main()
