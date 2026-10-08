#!/usr/bin/env python3
import subprocess
import sys
from pathlib import Path

BASE = Path(__file__).parent
VENV_PYTHON = BASE / ".venv" / "bin" / "python"


def main():
    print("▶  Running setup…", flush=True)
    subprocess.run([sys.executable, str(BASE / "setup.py")], check=True,
                   stdout=sys.stdout, stderr=sys.stderr)
    print("▶  Starting bot…", flush=True)
    if not VENV_PYTHON.exists():
        print(f"ERROR: venv python not found at {VENV_PYTHON}", flush=True)
        sys.exit(1)
    subprocess.run([str(VENV_PYTHON), str(BASE / "main.py")], check=True,
                   stdout=sys.stdout, stderr=sys.stderr)


if __name__ == "__main__":
    main()
