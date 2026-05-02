from __future__ import annotations

import json
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen


PROJECT_ROOT = Path(__file__).resolve().parent
BASE_URL = "http://127.0.0.1:5010"
KST = timezone(timedelta(hours=9), name="KST")


def kst_now() -> datetime:
    return datetime.now(KST)


def request_json(path: str, *, method: str = "GET", payload: dict | None = None, timeout: int = 15) -> dict:
    data = None
    headers = {}
    if payload is not None:
        data = json.dumps(payload).encode("utf-8")
        headers["Content-Type"] = "application/json"

    request = Request(f"{BASE_URL}{path}", data=data, headers=headers, method=method)
    with urlopen(request, timeout=timeout) as response:
        return json.loads(response.read().decode("utf-8"))


def stop_crawl_quietly() -> None:
    try:
        request_json("/crawl/stop", method="POST", payload={})
    except Exception:
        return


def main() -> int:
    try:
        health = request_json("/health")
    except (HTTPError, URLError) as exc:
        print(f"[FAIL] Hb server is not reachable at {BASE_URL}: {exc}")
        return 1

    if health.get("status") != "ok":
        print(f"[FAIL] Unexpected health response: {health}")
        return 1

    stop_crawl_quietly()

    now = kst_now()
    scheduled_for = now + timedelta(minutes=1)
    slot = scheduled_for.strftime("%H:%M")
    keyword = f"hb-wait-test-{scheduled_for.strftime('%H%M')}"

    payload = {
        "source": "tiktok",
        "input_mode": "keyword",
        "keywords": [keyword],
        "limit": 2,
        "save_mode": "overwrite",
        "profile_mode": "automation",
        "test_schedule": {
            "timezone": "Asia/Seoul",
            "slots": [slot],
        },
    }

    print(f"[INFO] Arming minute test for slot {slot} KST with keyword={keyword}")
    start_data = request_json("/crawl/start", method="POST", payload=payload)
    print(f"[INFO] Start response: {start_data}")

    if not start_data.get("success"):
        print("[FAIL] Failed to arm minute test.")
        return 1

    deadline = time.time() + 150
    trigger_seen = False
    finished = False
    last_status: dict = {}
    last_logs: list[str] = []

    while time.time() < deadline:
        last_status = request_json("/crawl/status")
        logs_response = request_json(f"/logs?{urlencode({'show_polling': 1})}")
        last_logs = logs_response.get("logs", [])

        joined = "\n".join(last_logs[-120:])
        if "scheduled batch triggered" in joined and slot in joined:
            trigger_seen = True

        tiktok_details = (last_status.get("last_details") or {}).get("tiktok") or {}
        if (
            tiktok_details.get("trigger") == "minute_test"
            and tiktok_details.get("scheduled_slot_label") == slot
            and tiktok_details.get("input_value") == keyword
        ):
            finished = True
            break

        time.sleep(5)

    stop_crawl_quietly()

    if not trigger_seen:
        print("[FAIL] Scheduled trigger log was not observed.")
        print("[DEBUG] Recent logs:")
        for line in last_logs[-30:]:
            print(line)
        return 1

    if not finished:
        print("[FAIL] Minute test did not produce the expected completion state before timeout.")
        print("[DEBUG] Last status:")
        print(json.dumps(last_status, ensure_ascii=False, indent=2))
        print("[DEBUG] Recent logs:")
        for line in last_logs[-30:]:
            print(line)
        return 1

    tiktok_details = (last_status.get("last_details") or {}).get("tiktok") or {}
    print("[PASS] Minute test completed.")
    print(
        "[PASS] "
        f"trigger={tiktok_details.get('trigger')} / slot={tiktok_details.get('scheduled_slot_label')} / "
        f"input={tiktok_details.get('input_value')} / discovered={tiktok_details.get('discovered_count')} / "
        f"saved={tiktok_details.get('saved_count')} / scheduled_for={tiktok_details.get('scheduled_for_kst')}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
