from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path
from urllib import error, request


PROJECT_ROOT = Path(__file__).resolve().parent
os.environ.setdefault("TIKTOK_WORKSHEET_NAME", "TikTok_Hb")


def _resolve_server_port() -> int:
    raw_value = os.environ.get("MYSELLCOMB_PORT")
    try:
        parsed = int(str(raw_value).strip())
    except (TypeError, ValueError):
        return 5010
    return parsed if parsed > 0 else 5010


SERVER_PORT = _resolve_server_port()
HEALTH_URL = f"http://127.0.0.1:{SERVER_PORT}/health"
FETCH_URL = f"http://127.0.0.1:{SERVER_PORT}/fetch/tiktok/auto"
KEYWORDS = ["다이소", "어린이날", "어버이날"]
SAVE_TARGET_COUNT = 2
SAVE_MODE = "overwrite"
PROFILE_MODE = "automation"
KEYWORD_DELAY_SECONDS = 8
HEALTH_RETRY_SECONDS = 2
HEALTH_RETRY_COUNT = 20
REQUEST_TIMEOUT_SECONDS = 900
LOG_PATH = PROJECT_ROOT / "data" / "tiktok_keyword_cycle.log"


def log(message: str) -> None:
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    line = f"[{timestamp}] {message}"
    LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
    with LOG_PATH.open("a", encoding="utf-8") as handle:
        handle.write(line + "\n")
    output_encoding = sys.stdout.encoding or "utf-8"
    safe_line = line.encode(output_encoding, errors="replace").decode(output_encoding, errors="replace")
    print(safe_line, flush=True)


def read_json(url: str, payload: dict | None = None) -> tuple[int, dict]:
    data = None
    headers = {}
    method = "GET"

    if payload is not None:
        method = "POST"
        data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        headers["Content-Type"] = "application/json; charset=utf-8"

    req = request.Request(url, data=data, headers=headers, method=method)

    try:
        with request.urlopen(req, timeout=REQUEST_TIMEOUT_SECONDS) as response:
            body = response.read().decode("utf-8")
            return response.status, json.loads(body)
    except error.HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace")
        try:
            parsed = json.loads(body)
        except Exception:
            parsed = {"success": False, "error": body}
        return exc.code, parsed


def ensure_server_running() -> None:
    try:
        status, payload = read_json(HEALTH_URL)
        if status == 200 and payload.get("status") == "ok":
            log("Hb서버 health check passed.")
            return
    except Exception as exc:
        log(f"Hb서버 health check failed before restart: {exc}")

    log("Hb서버 is down. Launching local server.")
    result = subprocess.run(
        [sys.executable, str(PROJECT_ROOT / "launch_dashboard.py")],
        cwd=PROJECT_ROOT,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
    )
    if result.stdout.strip():
        log(f"Launch output: {result.stdout.strip()}")
    if result.stderr.strip():
        log(f"Launch stderr: {result.stderr.strip()}")

    for attempt in range(HEALTH_RETRY_COUNT):
        try:
            status, payload = read_json(HEALTH_URL)
            if status == 200 and payload.get("status") == "ok":
                log("Hb서버 restarted successfully.")
                return
        except Exception as exc:
            log(f"Health retry {attempt + 1}/{HEALTH_RETRY_COUNT} failed: {exc}")
        time.sleep(HEALTH_RETRY_SECONDS)

    raise RuntimeError("Hb서버 did not recover after restart attempts.")


def run_keyword(keyword: str) -> dict:
    payload = {
        "input_mode": "keyword",
        "input_value": keyword,
        "limit": SAVE_TARGET_COUNT,
        "save_mode": SAVE_MODE,
        "profile_mode": PROFILE_MODE,
    }
    status, data = read_json(FETCH_URL, payload=payload)
    return {
        "keyword": keyword,
        "http_status": status,
        "success": status == 200 and bool(data.get("success")),
        "saved_count": data.get("saved_count"),
        "discovered_count": data.get("discovered_count"),
        "save_target_count": data.get("save_target_count"),
        "login_state": data.get("login_state"),
        "error": data.get("error") or data.get("save_error"),
    }


def main() -> int:
    ensure_server_running()
    log(
        "Starting Hb서버 TikTok keyword cycle: "
        f"keywords={KEYWORDS}, save_target={SAVE_TARGET_COUNT}, save_mode={SAVE_MODE}, profile_mode={PROFILE_MODE}, port={SERVER_PORT}"
    )

    failures = 0
    for index, keyword in enumerate(KEYWORDS):
        log(f"Running keyword {index + 1}/{len(KEYWORDS)}: {keyword}")
        try:
            result = run_keyword(keyword)
        except Exception as exc:
            failures += 1
            log(f"[ERROR] keyword={keyword} request failed: {exc}")
            result = None

        if result:
            if result["success"]:
                log(
                    "Completed "
                    f"keyword={result['keyword']} / discovered={result['discovered_count']} "
                    f"/ saved={result['saved_count']} / session={result['login_state']}"
                )
            else:
                failures += 1
                log(
                    "[ERROR] "
                    f"keyword={result['keyword']} / http_status={result['http_status']} "
                    f"/ discovered={result['discovered_count']} / saved={result['saved_count']} "
                    f"/ session={result['login_state']} / error={result['error']}"
                )

        if index < len(KEYWORDS) - 1:
            time.sleep(KEYWORD_DELAY_SECONDS)

    if failures:
        log(f"Hb서버 TikTok keyword cycle finished with failures={failures}.")
        return 1

    log("Hb서버 TikTok keyword cycle finished successfully.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
