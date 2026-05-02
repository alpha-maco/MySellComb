from __future__ import annotations

import json
import subprocess
import sys
import time
from pathlib import Path
from urllib import error, request


PROJECT_ROOT = Path(__file__).resolve().parent
HEALTH_RETRY_COUNT = 20
HEALTH_RETRY_SECONDS = 1
HEALTH_TIMEOUT_SECONDS = 5

SERVER_CONFIGS = (
    {
        "name": "Live",
        "root": PROJECT_ROOT,
        "port": 5000,
        "launch_script": PROJECT_ROOT / "launch_dashboard.py",
    },
    {
        "name": "Hb서버",
        "root": PROJECT_ROOT / "MySellComb_Hb",
        "port": 5010,
        "launch_script": PROJECT_ROOT / "MySellComb_Hb" / "launch_dashboard.py",
    },
)


def health_url(port: int) -> str:
    return f"http://127.0.0.1:{port}/health"


def read_health(port: int) -> tuple[bool, str]:
    req = request.Request(health_url(port), method="GET")
    try:
        with request.urlopen(req, timeout=HEALTH_TIMEOUT_SECONDS) as response:
            body = response.read().decode("utf-8")
            payload = json.loads(body)
            is_ok = response.status == 200 and payload.get("status") == "ok"
            return is_ok, body
    except error.HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace")
        return False, f"HTTP {exc.code}: {body}"
    except Exception as exc:
        return False, str(exc)


def ensure_server(server: dict) -> bool:
    ok, detail = read_health(server["port"])
    if ok:
        print(f"[OK] {server['name']} already healthy on {server['port']}")
        return True

    print(f"[WAIT] {server['name']} is down on {server['port']} ({detail})")
    result = subprocess.run(
        [sys.executable, str(server["launch_script"])],
        cwd=server["root"],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=30,
        check=False,
    )
    if result.stdout.strip():
        print(f"[LAUNCH] {server['name']} stdout: {result.stdout.strip()}")
    if result.stderr.strip():
        print(f"[LAUNCH] {server['name']} stderr: {result.stderr.strip()}")
    if result.returncode != 0:
        print(f"[ERROR] {server['name']} launcher returned {result.returncode}")
        return False

    for attempt in range(HEALTH_RETRY_COUNT):
        time.sleep(HEALTH_RETRY_SECONDS)
        ok, detail = read_health(server["port"])
        if ok:
            print(f"[OK] {server['name']} started on {server['port']}")
            return True
        print(
            f"[RETRY] {server['name']} health {attempt + 1}/{HEALTH_RETRY_COUNT} "
            f"not ready ({detail})"
        )

    print(f"[ERROR] {server['name']} did not become healthy on {server['port']}")
    return False


def main() -> int:
    failures = 0
    for server in SERVER_CONFIGS:
        if not ensure_server(server):
            failures += 1
    return 0 if failures == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
