from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parent
STDOUT_LOG = PROJECT_ROOT / "hb_server_stdout.log"
STDERR_LOG = PROJECT_ROOT / "hb_server_stderr.log"


def build_env() -> dict[str, str]:
    env: dict[str, str] = {}
    path_value = os.environ.get("Path") or os.environ.get("PATH") or ""

    for key, value in os.environ.items():
        if key.lower() == "path":
            continue
        env[key] = value

    env["PATH"] = path_value
    env.setdefault("MYSELLCOMB_HOST", "127.0.0.1")
    env.setdefault("MYSELLCOMB_PORT", "5010")
    env.setdefault("MYSELLCOMB_DEBUG", "0")
    env.setdefault("MYSELLCOMB_USE_RELOADER", "0")
    env.setdefault("MYSELLCOMB_OPEN_BROWSER", "0")
    env.setdefault("MYSELLCOMB_BROWSER_PROFILE_ROOT", str(PROJECT_ROOT / "crawler" / "browser_profile"))
    env.setdefault("TIKTOK_WORKSHEET_NAME", "TikTok_Hb")
    return env


def main() -> int:
    stdout_handle = STDOUT_LOG.open("w", encoding="utf-8")
    stderr_handle = STDERR_LOG.open("w", encoding="utf-8")

    creation_flags = 0
    if os.name == "nt":
        creation_flags = subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP
        creation_flags |= getattr(subprocess, "CREATE_BREAKAWAY_FROM_JOB", 0)

    process = subprocess.Popen(
        [sys.executable, "app.py"],
        cwd=PROJECT_ROOT,
        env=build_env(),
        stdin=subprocess.DEVNULL,
        stdout=stdout_handle,
        stderr=stderr_handle,
        creationflags=creation_flags,
    )

    stdout_handle.close()
    stderr_handle.close()
    print(process.pid)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
