from __future__ import annotations

import json
import sys
import time
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen


BASE_URL = "http://127.0.0.1:5010"
KST = timezone(timedelta(hours=9), name="KST")


@dataclass
class TestResult:
    name: str
    passed: bool
    details: str


def kst_now() -> datetime:
    return datetime.now(KST)


def request_json(path: str, *, method: str = "GET", payload: dict | None = None, timeout: int = 20) -> dict:
    data = None
    headers = {}
    if payload is not None:
        data = json.dumps(payload).encode("utf-8")
        headers["Content-Type"] = "application/json"

    req = Request(f"{BASE_URL}{path}", data=data, headers=headers, method=method)
    try:
        with urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except HTTPError as exc:
        body = exc.read().decode("utf-8", errors="ignore")
        try:
            data = json.loads(body) if body else {}
        except json.JSONDecodeError:
            data = {"raw_body": body}
        data.setdefault("success", False)
        data["_http_status"] = exc.code
        return data


def stop_crawl_quietly() -> None:
    try:
        request_json("/crawl/stop", method="POST", payload={})
    except Exception:
        pass


def wait_until_idle(timeout_seconds: int = 180) -> bool:
    deadline = time.time() + timeout_seconds
    while time.time() < deadline:
        status = request_json("/crawl/status")
        if not status.get("is_running"):
            return True
        time.sleep(1)
    return False


def build_keyword(tag: str, dt: datetime) -> str:
    return f"hb-{tag}-{dt.strftime('%H%M')}"


def count_finish_logs_for_keyword(keyword: str) -> int:
    logs = request_json(f"/logs?{urlencode({'show_polling': 1})}").get("logs", [])
    return sum(
        1
        for line in logs
        if "tiktok auto fetch finished" in line and f"input={keyword}" in line
    )


def arm_minute_test(keyword: str, slots: list[str], limit: int = 2) -> dict:
    payload = {
        "source": "tiktok",
        "input_mode": "keyword",
        "keywords": [keyword],
        "limit": limit,
        "save_mode": "overwrite",
        "profile_mode": "automation",
        "test_schedule": {
            "timezone": "Asia/Seoul",
            "slots": slots,
        },
    }
    return request_json("/crawl/start", method="POST", payload=payload)


def prepare_test_run() -> bool:
    stop_crawl_quietly()
    return wait_until_idle()


def wait_for_keyword_completion(keyword: str, expected_slots: set[str], timeout_seconds: int) -> tuple[set[str], dict]:
    seen_slots: set[str] = set()
    last_status: dict = {}
    deadline = time.time() + timeout_seconds

    while time.time() < deadline:
        last_status = request_json("/crawl/status")
        details = (last_status.get("last_details") or {}).get("tiktok") or {}
        if details.get("input_value") == keyword and details.get("trigger") in {"minute_test", "scheduled_batch"}:
            slot_label = details.get("scheduled_slot_label")
            if slot_label:
                seen_slots.add(slot_label)
        if expected_slots.issubset(seen_slots):
            return seen_slots, last_status
        time.sleep(5)

    return seen_slots, last_status


def ensure_health() -> None:
    health = request_json("/health")
    if health.get("status") != "ok":
        raise RuntimeError(f"Unexpected health payload: {health}")


def run_single_wait_test() -> TestResult:
    if not prepare_test_run():
        return TestResult("single_wait", False, "failed to reach idle state before test start")

    now = kst_now()
    scheduled_for = now + timedelta(minutes=1)
    slot = scheduled_for.strftime("%H:%M")
    keyword = build_keyword("wait", scheduled_for)

    start_data = arm_minute_test(keyword, [slot])
    if not start_data.get("success"):
        return TestResult("single_wait", False, f"arm failed: {start_data}")

    seen_slots, last_status = wait_for_keyword_completion(keyword, {slot}, timeout_seconds=240)
    stop_crawl_quietly()
    wait_until_idle()

    if slot not in seen_slots:
        return TestResult("single_wait", False, f"slot {slot} did not complete; last_status={last_status}")

    details = (last_status.get("last_details") or {}).get("tiktok") or {}
    return TestResult(
        "single_wait",
        True,
        f"slot={slot} trigger={details.get('trigger')} discovered={details.get('discovered_count')} saved={details.get('saved_count')}",
    )


def run_multi_slot_test() -> TestResult:
    if not prepare_test_run():
        return TestResult("multi_slot", False, "failed to reach idle state before test start")

    now = kst_now()
    slot_a = (now + timedelta(minutes=1)).strftime("%H:%M")
    slot_b = (now + timedelta(minutes=2)).strftime("%H:%M")
    keyword = build_keyword("multi", now + timedelta(minutes=2))

    start_data = arm_minute_test(keyword, [slot_a, slot_b])
    if not start_data.get("success"):
        return TestResult("multi_slot", False, f"arm failed: {start_data}")

    seen_slots, last_status = wait_for_keyword_completion(keyword, {slot_a, slot_b}, timeout_seconds=420)
    stop_crawl_quietly()
    wait_until_idle()

    execution_count = int(last_status.get("execution_count") or 0)
    executed_slots = {
        item.get("slot")
        for item in (last_status.get("executed_schedule_slots") or [])
        if isinstance(item, dict)
    }
    missing = {slot_a, slot_b} - seen_slots
    if missing:
        return TestResult("multi_slot", False, f"missing_slots={sorted(missing)} last_status={last_status}")
    if {slot_a, slot_b} - executed_slots:
        return TestResult("multi_slot", False, f"executed_slots={sorted(executed_slots)}")
    if execution_count < 2:
        return TestResult("multi_slot", False, f"expected execution_count >= 2, got {execution_count}")

    return TestResult("multi_slot", True, f"slots={slot_a},{slot_b} execution_count={execution_count}")


def run_stop_cancel_test() -> TestResult:
    if not prepare_test_run():
        return TestResult("stop_cancel", False, "failed to reach idle state before test start")

    now = kst_now()
    slot = (now + timedelta(minutes=2)).strftime("%H:%M")
    keyword = build_keyword("stop", now + timedelta(minutes=2))

    start_data = arm_minute_test(keyword, [slot])
    if not start_data.get("success"):
        return TestResult("stop_cancel", False, f"arm failed: {start_data}")

    time.sleep(5)
    stop_crawl_quietly()
    wait_until_idle()

    deadline = time.time() + 210
    triggered = False
    last_status = {}
    while time.time() < deadline:
        status = request_json("/crawl/status")
        last_status = status
        details = (status.get("last_details") or {}).get("tiktok") or {}
        if details.get("input_value") == keyword and details.get("scheduled_slot_label") == slot:
            triggered = True
            break
        time.sleep(5)

    execution_count = int((last_status or {}).get("execution_count") or 0)
    if triggered or execution_count > 0:
        return TestResult("stop_cancel", False, f"keyword executed after stop; execution_count={execution_count}")

    return TestResult("stop_cancel", True, f"slot={slot} canceled before execution")


def run_duplicate_guard_test() -> TestResult:
    if not prepare_test_run():
        return TestResult("duplicate_guard", False, "failed to reach idle state before test start")

    now = kst_now()
    slot = (now + timedelta(minutes=1)).strftime("%H:%M")
    keyword = build_keyword("dup", now + timedelta(minutes=1))

    start_data = arm_minute_test(keyword, [slot])
    if not start_data.get("success"):
        return TestResult("duplicate_guard", False, f"arm failed: {start_data}")

    seen_slots, last_status = wait_for_keyword_completion(keyword, {slot}, timeout_seconds=240)
    time.sleep(75)
    stop_crawl_quietly()
    wait_until_idle()

    execution_count = int(last_status.get("execution_count") or 0)
    executed_slots = [
        item.get("slot")
        for item in (last_status.get("executed_schedule_slots") or [])
        if isinstance(item, dict)
    ]
    if slot not in seen_slots:
        return TestResult("duplicate_guard", False, f"slot {slot} never completed; last_status={last_status}")
    if execution_count != 1:
        return TestResult("duplicate_guard", False, f"expected execution_count=1, got {execution_count}")
    if executed_slots.count(slot) != 1:
        return TestResult("duplicate_guard", False, f"expected slot count 1, got {executed_slots}")

    return TestResult("duplicate_guard", True, f"slot={slot} execution_count={execution_count}")


def main() -> int:
    try:
        ensure_health()
    except (HTTPError, URLError, RuntimeError) as exc:
        print(f"[FAIL] Hb server health check failed: {exc}")
        return 1

    stop_crawl_quietly()
    wait_until_idle()

    available = {
        "single_wait": run_single_wait_test,
        "multi_slot": run_multi_slot_test,
        "stop_cancel": run_stop_cancel_test,
        "duplicate_guard": run_duplicate_guard_test,
    }

    selected_names = sys.argv[1:] or list(available.keys())
    invalid = [name for name in selected_names if name not in available]
    if invalid:
        print(f"[FAIL] Unknown test names: {', '.join(invalid)}")
        print(f"[INFO] Available tests: {', '.join(available)}")
        return 1

    results = [available[name]() for name in selected_names]

    failures = [result for result in results if not result.passed]
    for result in results:
        level = "PASS" if result.passed else "FAIL"
        print(f"[{level}] {result.name}: {result.details}")

    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
