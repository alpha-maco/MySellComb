import json
import os
import subprocess
import threading
import time
from datetime import datetime, timedelta, timezone
from importlib import import_module
from pathlib import Path

import bootstrap
from flask import Flask, jsonify, render_template, request

app = Flask(__name__)

PROJECT_ROOT = Path(__file__).resolve().parent

FETCHER_IMPORTS = {
    "naver": ("crawler.naver_fetcher", "fetch"),
    "coupang": ("crawler.coupang_fetcher", "fetch"),
}

AVAILABLE_SOURCES = ["coupang", "naver", "tiktok"]

CHROME_PATH = r"C:\Program Files\Google\Chrome\Application\chrome.exe"

HTTP_LOGS = []
MAX_LOGS = 300
POLLING_LOG_PATHS = {
    "/logs",
    "/crawl/status",
}

TIKTOK_BATCH_SLOTS = tuple(f"{hour:02d}:00" for hour in range(24))
DEFAULT_TIKTOK_BATCH_ACTIVE_SLOTS = ("03:00", "04:00", "05:00", "06:00", "07:00")
DEFAULT_TIKTOK_BATCH_SCHEDULE = {
    "timezone": "Asia/Seoul",
    "weekday": list(DEFAULT_TIKTOK_BATCH_ACTIVE_SLOTS),
    "weekend": list(DEFAULT_TIKTOK_BATCH_ACTIVE_SLOTS),
}
KST = timezone(timedelta(hours=9), name="KST")

CRAWL_STATE = {
    "is_running": False,
    "source": None,
    "mode": None,
    "interval_seconds": None,
    "next_run_at": None,
    "last_started_at": None,
    "last_stopped_at": None,
    "last_completed_at": {},
    "last_result_count": {},
    "last_error": {},
    "last_details": {},
    "active_config": None,
    "last_schedule_slot": None,
    "execution_count": 0,
    "executed_schedule_slots": [],
    "thread": None,
    "stop_event": None,
}
STATE_LOCK = threading.Lock()


def utc_now():
    return datetime.now(timezone.utc)


def fmt_utc(dt):
    if not dt:
        return None
    return dt.strftime("%Y-%m-%d %H:%M:%S.%f")[:-3] + " UTC"


def kst_now():
    return datetime.now(KST)


def fmt_kst(dt):
    if not dt:
        return None
    return dt.astimezone(KST).strftime("%Y-%m-%d %H:%M:%S KST")


def get_data_root() -> Path:
    configured_root = (os.environ.get("MYSELLCOMB_DATA_ROOT") or "").strip()
    data_root = Path(configured_root) if configured_root else PROJECT_ROOT / "data"
    data_root.mkdir(parents=True, exist_ok=True)
    return data_root


def get_tiktok_batch_schedule_path() -> Path:
    return get_data_root() / "tiktok_batch_schedule.json"


def normalize_tiktok_batch_schedule(payload: dict | None) -> dict:
    source = payload if isinstance(payload, dict) else {}
    normalized = {
        "timezone": DEFAULT_TIKTOK_BATCH_SCHEDULE["timezone"],
        "available_slots": list(TIKTOK_BATCH_SLOTS),
    }

    for day_type in ("weekday", "weekend"):
        raw_values = source.get(day_type, DEFAULT_TIKTOK_BATCH_SCHEDULE[day_type])
        if not isinstance(raw_values, list):
            raw_values = DEFAULT_TIKTOK_BATCH_SCHEDULE[day_type]

        selected_values = {str(value).strip() for value in raw_values}
        normalized[day_type] = [slot for slot in TIKTOK_BATCH_SLOTS if slot in selected_values]

    return normalized


def normalize_daily_time_slots(raw_values) -> list[str]:
    if not isinstance(raw_values, list):
        return []

    normalized = []
    seen = set()

    for raw_value in raw_values:
        value = str(raw_value or "").strip()
        if len(value) != 5 or value[2] != ":":
            continue

        hour_part, minute_part = value.split(":", 1)
        if not (hour_part.isdigit() and minute_part.isdigit()):
            continue

        hour = int(hour_part)
        minute = int(minute_part)
        if not (0 <= hour <= 23 and 0 <= minute <= 59):
            continue

        slot = f"{hour:02d}:{minute:02d}"
        if slot in seen:
            continue

        seen.add(slot)
        normalized.append(slot)

    return sorted(normalized)


def load_tiktok_batch_schedule() -> dict:
    schedule_path = get_tiktok_batch_schedule_path()
    if not schedule_path.exists():
        return normalize_tiktok_batch_schedule(DEFAULT_TIKTOK_BATCH_SCHEDULE)

    try:
        payload = json.loads(schedule_path.read_text(encoding="utf-8"))
    except Exception:
        payload = DEFAULT_TIKTOK_BATCH_SCHEDULE

    return normalize_tiktok_batch_schedule(payload)


def save_tiktok_batch_schedule(schedule: dict) -> dict:
    normalized = normalize_tiktok_batch_schedule(schedule)
    schedule_path = get_tiktok_batch_schedule_path()
    schedule_path.write_text(json.dumps(normalized, ensure_ascii=False, indent=2), encoding="utf-8")
    return normalized


def dedupe_text_values(values) -> list[str]:
    seen = set()
    normalized = []
    for raw_value in values:
        value = str(raw_value or "").strip()
        if not value or value in seen:
            continue
        seen.add(value)
        normalized.append(value)
    return normalized


def has_any_tiktok_batch_slots(schedule: dict) -> bool:
    return bool(schedule.get("weekday") or schedule.get("weekend"))


def get_tiktok_day_type(now_local: datetime) -> str:
    return "weekend" if now_local.weekday() >= 5 else "weekday"


def get_matching_tiktok_schedule_slot(schedule: dict, now_local: datetime, grace_minutes: int = 5) -> tuple[str, str] | None:
    if now_local.minute >= grace_minutes:
        return None

    day_type = get_tiktok_day_type(now_local)
    slot_label = f"{now_local.hour:02d}:00"
    if slot_label not in schedule.get(day_type, []):
        return None

    slot_key = f"{now_local.strftime('%Y-%m-%d')} {slot_label} {day_type}"
    return day_type, slot_key


def get_matching_daily_time_slot(slots: list[str], now_local: datetime, grace_seconds: int = 59) -> str | None:
    slot_label = f"{now_local.hour:02d}:{now_local.minute:02d}"
    if slot_label not in slots:
        return None
    if now_local.second > grace_seconds:
        return None
    return slot_label


def get_next_tiktok_run_at(schedule: dict, now_local: datetime, grace_minutes: int = 5) -> datetime | None:
    for offset in range(0, 8):
        candidate = now_local + timedelta(days=offset)
        candidate_date = candidate.date()
        candidate_day_type = get_tiktok_day_type(datetime(candidate.year, candidate.month, candidate.day, tzinfo=KST))
        for slot_label in schedule.get(candidate_day_type, []):
            hour = int(slot_label.split(":", 1)[0])
            candidate_dt = datetime(
                candidate_date.year,
                candidate_date.month,
                candidate_date.day,
                hour,
                0,
                tzinfo=KST,
            )
            if offset == 0:
                if candidate_dt > now_local:
                    return candidate_dt
                if candidate_dt <= now_local < (candidate_dt + timedelta(minutes=grace_minutes)):
                    return candidate_dt
                continue
            return candidate_dt
    return None


def get_next_daily_time_slot_run_at(slots: list[str], now_local: datetime) -> datetime | None:
    for offset in range(0, 3):
        candidate = now_local + timedelta(days=offset)
        candidate_date = candidate.date()

        for slot_label in slots:
            hour, minute = [int(part) for part in slot_label.split(":", 1)]
            candidate_dt = datetime(
                candidate_date.year,
                candidate_date.month,
                candidate_date.day,
                hour,
                minute,
                tzinfo=KST,
            )
            if offset == 0:
                if candidate_dt >= now_local:
                    return candidate_dt
                continue
            return candidate_dt
    return None


def collect_due_minute_test_slots(slots: list[str], window_start: datetime, window_end: datetime) -> list[tuple[datetime, str, str, str]]:
    due_slots = []
    start_date = window_start.date()
    end_date = window_end.date()
    day_count = (end_date - start_date).days

    for offset in range(day_count + 1):
        current_date = start_date + timedelta(days=offset)
        for slot_label in slots:
            hour, minute = [int(part) for part in slot_label.split(":", 1)]
            candidate_dt = datetime(
                current_date.year,
                current_date.month,
                current_date.day,
                hour,
                minute,
                tzinfo=KST,
            )
            if window_start < candidate_dt <= window_end:
                due_slots.append(
                    (
                        candidate_dt,
                        slot_label,
                        "daily",
                        f"{current_date.isoformat()} {slot_label} minute_test",
                    )
                )

    return sorted(due_slots, key=lambda item: item[0])


def collect_due_batch_schedule_slots(schedule: dict, window_start: datetime, window_end: datetime) -> list[tuple[datetime, str, str, str]]:
    due_slots = []
    start_date = window_start.date()
    end_date = window_end.date()
    day_count = (end_date - start_date).days

    for offset in range(day_count + 1):
        current_date = start_date + timedelta(days=offset)
        current_day_type = get_tiktok_day_type(
            datetime(current_date.year, current_date.month, current_date.day, tzinfo=KST)
        )
        for slot_label in schedule.get(current_day_type, []):
            hour = int(slot_label.split(":", 1)[0])
            candidate_dt = datetime(
                current_date.year,
                current_date.month,
                current_date.day,
                hour,
                0,
                tzinfo=KST,
            )
            if window_start < candidate_dt <= window_end:
                due_slots.append(
                    (
                        candidate_dt,
                        slot_label,
                        current_day_type,
                        f"{current_date.isoformat()} {slot_label} {current_day_type}",
                    )
                )

    return sorted(due_slots, key=lambda item: item[0])


def build_tiktok_repeat_config(payload: dict) -> tuple[dict | None, str | None]:
    input_mode = str(payload.get("input_mode", "keyword")).strip().lower() or "keyword"
    save_mode = str(payload.get("save_mode", "overwrite")).strip().lower() or "overwrite"
    profile_mode = str(payload.get("profile_mode", "default")).strip().lower() or "default"
    limit = parse_positive_int(payload.get("limit", 10), 10)
    schedule = normalize_tiktok_batch_schedule(payload.get("schedule") or load_tiktok_batch_schedule())
    test_schedule_source = payload.get("test_schedule") or {}
    test_slots = normalize_daily_time_slots(test_schedule_source.get("slots") or [])

    if input_mode not in {"keyword", "url"}:
        return None, "input_mode must be 'keyword' or 'url'."
    if save_mode not in {"overwrite", "append"}:
        return None, "save_mode must be 'overwrite' or 'append'."
    if profile_mode not in {"default", "automation"}:
        return None, "profile_mode must be 'default' or 'automation'."
    schedule_type = "batch_schedule"
    if test_slots:
        schedule_type = "minute_test"
    elif not has_any_tiktok_batch_slots(schedule):
        return None, "At least one TikTok batch slot must be selected before starting repeat crawl."

    raw_inputs = []
    if input_mode == "keyword":
        keyword_values = payload.get("keywords") or payload.get("keyword_slots") or []
        if isinstance(keyword_values, list):
            raw_inputs.extend(keyword_values)
        elif keyword_values:
            raw_inputs.append(keyword_values)
        raw_inputs.append(payload.get("input_value") or payload.get("keyword") or "")
    else:
        url_values = payload.get("urls") or []
        if isinstance(url_values, list):
            raw_inputs.extend(url_values)
        elif url_values:
            raw_inputs.append(url_values)
        raw_inputs.append(payload.get("input_value") or payload.get("video_url") or payload.get("url") or "")

    input_values = dedupe_text_values(raw_inputs)
    if not input_values:
        field_label = "keyword" if input_mode == "keyword" else "video URL"
        return None, f"At least one TikTok {field_label} is required for repeat crawl."

    return {
        "schedule_type": schedule_type,
        "input_mode": input_mode,
        "input_values": input_values,
        "limit": limit,
        "save_mode": save_mode,
        "profile_mode": profile_mode,
        "schedule": schedule,
        "test_schedule": {
            "timezone": "Asia/Seoul",
            "slots": test_slots,
        },
    }, None


def add_http_log(message: str, is_polling: bool = False):
    timestamp = fmt_utc(utc_now())
    HTTP_LOGS.append({
        "message": f"[{timestamp}] {message}",
        "is_polling": is_polling,
    })
    if len(HTTP_LOGS) > MAX_LOGS:
        del HTTP_LOGS[0]


def update_result_state(source: str, count: int, error: str | None = None, details: dict | None = None):
    with STATE_LOCK:
        CRAWL_STATE["last_completed_at"][source] = utc_now()
        CRAWL_STATE["last_result_count"][source] = count
        CRAWL_STATE["last_error"][source] = error
        if details is not None:
            CRAWL_STATE["last_details"][source] = details


@app.after_request
def log_request(response):
    add_http_log(
        f'{request.remote_addr} "{request.method} {request.path}'
        f'{("?" + request.query_string.decode()) if request.query_string else ""}" {response.status_code}',
        is_polling=request.path in POLLING_LOG_PATHS,
    )
    return response


def get_fetcher(source: str):
    module_name, function_name = FETCHER_IMPORTS[source]
    module = import_module(module_name)
    return getattr(module, function_name)


def run_fetch_once(source: str):
    fetcher = get_fetcher(source)
    add_http_log(f"[NOTICE] [CRAWL] {source} fetch started")
    products = fetcher()
    count = len(products) if products else 0

    update_result_state(source, count, error=None, details={"mode": "single"})

    if count == 0:
        add_http_log(f"[NOTICE] [CRAWL] {source} fetch finished / 0 items")
    else:
        add_http_log(f"[CRAWL] {source} fetch finished / {count} items")

    return products


def build_tiktok_fetch_details(
    result: dict,
    profile_mode: str,
    limit: int,
    trigger: str,
    scheduled_slot_label: str | None = None,
    scheduled_day_type: str | None = None,
):
    return {
        "mode": "auto",
        "trigger": trigger,
        "keyword": result["keyword"],
        "input_value": result["input_value"],
        "input_mode": result["input_mode"],
        "save_mode": result["save_mode"],
        "profile_mode": result.get("profile_mode", profile_mode),
        "limit": limit,
        "save_target_count": result["save_target_count"],
        "discovery_limit": result["discovery_limit"],
        "discovered_count": result["discovered_count"],
        "saved_count": result["saved_count"],
        "sheet_name": result["sheet_name"],
        "save_error": result["save_error"],
        "blocked_reason": result["blocked_reason"],
        "login_state": result.get("login_state"),
        "login_state_before": result.get("login_state_before"),
        "login_state_reason": result.get("login_state_reason"),
        "removed_auth_cookie_names": result.get("removed_auth_cookie_names", []),
        "scheduled_slot_label": scheduled_slot_label,
        "scheduled_day_type": scheduled_day_type,
    }


def log_tiktok_fetch_result(result: dict):
    removed_auth_cookie_names = result.get("removed_auth_cookie_names", [])
    remaining_auth_cookie_names = result.get("auth_cookie_names", [])
    removed_auth_summary = (
        f" / removed_auth_cookies={','.join(removed_auth_cookie_names)}"
        if removed_auth_cookie_names
        else ""
    )
    remaining_auth_summary = (
        f" / remaining_auth_cookies={','.join(remaining_auth_cookie_names)}"
        if remaining_auth_cookie_names
        else ""
    )
    add_http_log(
        "[NOTICE] [TIKTOK] "
        f"session={result.get('login_state', 'unknown')} / "
        f"before={result.get('login_state_before', 'unknown')} / "
        f"reason={result.get('login_state_reason', 'unknown')}"
        f"{removed_auth_summary}{remaining_auth_summary}"
    )

    if result["blocked_reason"]:
        add_http_log(
            "[ERROR] [FETCH] "
            f"tiktok blocked / mode={result['input_mode']} / input={result['input_value']} / discovered={result['discovered_count']} / save_mode={result['save_mode']} / "
            f"saved={result['saved_count']} / session={result.get('login_state', 'unknown')} / "
            f"reason={result['blocked_reason']}"
        )
        return

    add_http_log(
        "[CRAWL] "
        f"tiktok auto fetch finished / mode={result['input_mode']} / input={result['input_value']} / discovered={result['discovered_count']} / save_mode={result['save_mode']} / "
        f"saved={result['saved_count']} / session={result.get('login_state', 'unknown')}"
    )
    if result["save_error"]:
        add_http_log(
            f"[ERROR] [SAVE] tiktok sheet save failed / mode={result['input_mode']} / input={result['input_value']} / {result['save_error']}"
        )


def invoke_tiktok_auto_fetch(input_mode: str, input_value: str, limit: int, save_mode: str, profile_mode: str):
    tiktok_auto_fetch = getattr(import_module("crawler.tiktok_fetcher"), "fetch_auto")
    return tiktok_auto_fetch(
        query=input_value,
        limit=limit,
        input_mode=input_mode,
        save_mode=save_mode,
        profile_mode=profile_mode,
    )


def set_next_run_at(next_run_at: datetime | None):
    with STATE_LOCK:
        CRAWL_STATE["next_run_at"] = next_run_at


def reset_active_crawl_state():
    with STATE_LOCK:
        CRAWL_STATE["is_running"] = False
        CRAWL_STATE["source"] = None
        CRAWL_STATE["mode"] = None
        CRAWL_STATE["interval_seconds"] = None
        CRAWL_STATE["next_run_at"] = None
        CRAWL_STATE["thread"] = None
        CRAWL_STATE["stop_event"] = None
        CRAWL_STATE["active_config"] = None
        CRAWL_STATE["last_schedule_slot"] = None
        CRAWL_STATE["last_stopped_at"] = utc_now()


def crawl_worker(source: str, interval_seconds: int, stop_event: threading.Event):
    add_http_log(f"[NOTICE] [CRAWL] repeat crawl started / source={source} / interval={interval_seconds}s")

    try:
        while not stop_event.is_set():
            try:
                run_fetch_once(source)
            except Exception as exc:
                update_result_state(source, 0, error=str(exc))
                add_http_log(f"[ERROR] [CRAWL] {source} run failed: {exc}")

            waited = 0
            while waited < interval_seconds and not stop_event.is_set():
                time.sleep(1)
                waited += 1

    finally:
        reset_active_crawl_state()
        add_http_log("[NOTICE] [CRAWL] repeat crawl stopped")


def run_tiktok_scheduled_batch(
    config: dict,
    scheduled_for: datetime,
    scheduled_slot_label: str,
    scheduled_day_type: str,
    trigger: str = "scheduled_batch",
):
    input_mode = config["input_mode"]
    limit = config["limit"]
    save_mode = config["save_mode"]
    profile_mode = config["profile_mode"]
    input_values = config["input_values"]

    add_http_log(
        "[NOTICE] [TIKTOK] "
        f"scheduled batch triggered / slot={scheduled_slot_label} / day_type={scheduled_day_type} / "
        f"scheduled_for={fmt_kst(scheduled_for)} / items={len(input_values)} / save_target={limit} / save_mode={save_mode}"
    )

    total_count = 0
    total_discovered = 0
    total_saved = 0
    batch_error = None
    aggregate_results = []

    with STATE_LOCK:
        previous_slots = list(CRAWL_STATE.get("executed_schedule_slots") or [])
        execution_count = int(CRAWL_STATE.get("execution_count") or 0) + 1
        current_slot_marker = {
            "slot": scheduled_slot_label,
            "day_type": scheduled_day_type,
            "scheduled_for_kst": fmt_kst(scheduled_for),
            "trigger": trigger,
        }
        completed_slots_history = previous_slots + [current_slot_marker]
        CRAWL_STATE["execution_count"] = execution_count
        CRAWL_STATE["executed_schedule_slots"] = completed_slots_history

    for input_value in input_values:
        add_http_log(
            f"[NOTICE] [FETCH] tiktok scheduled fetch started / mode={input_mode} / input={input_value} / save_target={limit} / save_mode={save_mode} / profile_mode={profile_mode}"
        )

        try:
            result = invoke_tiktok_auto_fetch(
                input_mode=input_mode,
                input_value=input_value,
                limit=limit,
                save_mode=save_mode,
                profile_mode=profile_mode,
            )
        except Exception as exc:
            batch_error = batch_error or str(exc)
            add_http_log(f"[ERROR] [FETCH] tiktok scheduled fetch failed / input={input_value} / {exc}")
            aggregate_results.append({
                "input_value": input_value,
                "success": False,
                "error": str(exc),
            })
            continue

        log_tiktok_fetch_result(result)

        total_count += len(result["products"])
        total_discovered += result["discovered_count"]
        total_saved += result["saved_count"]
        if result["blocked_reason"] or result["save_error"]:
            batch_error = batch_error or result["blocked_reason"] or result["save_error"]

        aggregate_results.append({
            "input_value": result["input_value"],
            "keyword": result["keyword"],
            "success": not bool(result["blocked_reason"]),
            "discovered_count": result["discovered_count"],
            "saved_count": result["saved_count"],
            "save_error": result["save_error"],
            "blocked_reason": result["blocked_reason"],
            "sheet_name": result["sheet_name"],
            "login_state": result.get("login_state"),
        })

    update_result_state(
        "tiktok",
        total_count,
        error=batch_error,
        details={
            "mode": trigger,
            "trigger": trigger,
            "input_mode": input_mode,
            "input_value": ", ".join(input_values),
            "input_values": list(input_values),
            "save_mode": save_mode,
            "profile_mode": profile_mode,
            "limit": limit,
            "save_target_count": limit,
            "discovered_count": total_discovered,
            "saved_count": total_saved,
            "scheduled_for_kst": fmt_kst(scheduled_for),
            "scheduled_slot_label": scheduled_slot_label,
            "scheduled_day_type": scheduled_day_type,
            "execution_count": execution_count,
            "completed_slots_history": completed_slots_history,
            "results": aggregate_results,
        },
    )

    add_http_log(
        "[NOTICE] [TIKTOK] "
        f"scheduled batch finished / slot={scheduled_slot_label} / day_type={scheduled_day_type} / discovered={total_discovered} / saved={total_saved} / "
        f"items={len(input_values)} / errors={batch_error or '-'}"
    )


def crawl_tiktok_schedule_worker(config: dict, stop_event: threading.Event):
    schedule_type = config.get("schedule_type", "batch_schedule")
    schedule = config["schedule"]
    test_schedule = config.get("test_schedule") or {}
    minute_slots = test_schedule.get("slots") or []
    last_executed_slot = None
    grace_delta = timedelta(seconds=59) if schedule_type == "minute_test" else timedelta(minutes=5)
    last_slot_scan_at = kst_now() - grace_delta

    if schedule_type == "minute_test":
        add_http_log(
            "[NOTICE] [CRAWL] "
            f"tiktok minute-test crawl started / slots={','.join(minute_slots) or '-'} / "
            f"inputs={','.join(config['input_values'])} / save_target={config['limit']} / save_mode={config['save_mode']}"
        )
    else:
        add_http_log(
            "[NOTICE] [CRAWL] "
            f"tiktok batch crawl started / weekdays={','.join(schedule['weekday']) or '-'} / weekend={','.join(schedule['weekend']) or '-'} / "
            f"inputs={','.join(config['input_values'])} / save_target={config['limit']} / save_mode={config['save_mode']}"
        )

    try:
        while not stop_event.is_set():
            now_local = kst_now()
            if schedule_type == "minute_test":
                due_slots = collect_due_minute_test_slots(minute_slots, last_slot_scan_at, now_local)
                next_run_at = get_next_daily_time_slot_run_at(minute_slots, now_local)
            else:
                due_slots = collect_due_batch_schedule_slots(schedule, last_slot_scan_at, now_local)
                next_run_at = get_next_tiktok_run_at(schedule, now_local)

            set_next_run_at(next_run_at)

            with STATE_LOCK:
                CRAWL_STATE["last_schedule_slot"] = last_executed_slot

            ran_any_slot = False
            for scheduled_for, scheduled_slot_label, scheduled_day_type, slot_key in due_slots:
                if stop_event.is_set():
                    break
                if slot_key == last_executed_slot:
                    continue

                last_executed_slot = slot_key
                with STATE_LOCK:
                    CRAWL_STATE["last_schedule_slot"] = slot_key
                run_tiktok_scheduled_batch(
                    config=config,
                    scheduled_for=scheduled_for,
                    scheduled_slot_label=scheduled_slot_label,
                    scheduled_day_type=scheduled_day_type,
                    trigger="minute_test" if schedule_type == "minute_test" else "scheduled_batch",
                )
                last_slot_scan_at = scheduled_for
                ran_any_slot = True
                break

            if ran_any_slot:
                continue

            last_slot_scan_at = now_local

            if stop_event.wait(5):
                break

    finally:
        reset_active_crawl_state()
        add_http_log("[NOTICE] [CRAWL] repeat crawl stopped")


def parse_positive_int(raw_value, default_value: int) -> int:
    try:
        parsed = int(raw_value)
    except (TypeError, ValueError):
        return default_value
    return parsed if parsed > 0 else default_value


def parse_bool_env(name: str, default_value: bool) -> bool:
    raw_value = os.environ.get(name)
    if raw_value is None:
        return default_value
    return raw_value.strip().lower() in {"1", "true", "yes", "on"}


def get_runtime_settings() -> dict:
    port = parse_positive_int(os.environ.get("MYSELLCOMB_PORT"), 5000)
    debug_enabled = parse_bool_env("MYSELLCOMB_DEBUG", True)
    use_reloader = parse_bool_env("MYSELLCOMB_USE_RELOADER", debug_enabled)
    open_browser = parse_bool_env("MYSELLCOMB_OPEN_BROWSER", debug_enabled)
    host = os.environ.get("MYSELLCOMB_HOST", "127.0.0.1").strip() or "127.0.0.1"
    browser_url = os.environ.get("MYSELLCOMB_BROWSER_URL", f"http://127.0.0.1:{port}/").strip()
    return {
        "host": host,
        "port": port,
        "debug": debug_enabled,
        "use_reloader": use_reloader,
        "open_browser": open_browser,
        "browser_url": browser_url or f"http://127.0.0.1:{port}/",
    }


@app.route("/")
def index():
    return render_template("index.html", products=[])


@app.route("/health")
def health():
    return jsonify({"status": "ok", "available_sources": AVAILABLE_SOURCES})


@app.route("/settings/tiktok-batch-schedule", methods=["GET", "POST"])
def tiktok_batch_schedule():
    if request.method == "GET":
        return jsonify({"success": True, "schedule": load_tiktok_batch_schedule()})

    data = request.get_json(silent=True) or {}
    schedule = save_tiktok_batch_schedule(data)
    add_http_log(
        "[NOTICE] [TIKTOK] batch schedule saved / "
        f"weekday={','.join(schedule['weekday']) or '-'} / "
        f"weekend={','.join(schedule['weekend']) or '-'} / "
        f"timezone={schedule['timezone']}"
    )
    return jsonify({"success": True, "message": "TikTok batch schedule saved.", "schedule": schedule})


@app.route("/logs")
def logs():
    show_polling = str(request.args.get("show_polling", "")).strip().lower() in {"1", "true", "yes", "on"}
    visible_logs = [
        entry["message"]
        for entry in HTTP_LOGS
        if show_polling or not entry["is_polling"]
    ]
    return jsonify({"success": True, "logs": visible_logs[-120:], "show_polling": show_polling})


@app.route("/crawl/status")
def crawl_status():
    with STATE_LOCK:
        payload = {
            "success": True,
            "is_running": CRAWL_STATE["is_running"],
            "source": CRAWL_STATE["source"],
            "mode": CRAWL_STATE["mode"],
            "interval_seconds": CRAWL_STATE["interval_seconds"],
            "next_run_at_kst": fmt_kst(CRAWL_STATE["next_run_at"]),
            "last_started_at": fmt_utc(CRAWL_STATE["last_started_at"]),
            "last_stopped_at": fmt_utc(CRAWL_STATE["last_stopped_at"]),
            "last_completed_at": {
                key: fmt_utc(value) for key, value in CRAWL_STATE["last_completed_at"].items()
            },
            "last_result_count": CRAWL_STATE["last_result_count"],
            "last_error": CRAWL_STATE["last_error"],
            "last_details": CRAWL_STATE["last_details"],
            "active_config": CRAWL_STATE["active_config"],
            "last_schedule_slot": CRAWL_STATE["last_schedule_slot"],
            "execution_count": CRAWL_STATE["execution_count"],
            "executed_schedule_slots": CRAWL_STATE["executed_schedule_slots"],
        }
    return jsonify(payload)


@app.route("/crawl/start", methods=["POST"])
def crawl_start():
    data = request.get_json(silent=True) or {}

    source = str(data.get("source", "")).strip().lower()
    interval_seconds = parse_positive_int(data.get("interval_seconds", 0), 0)
    force = bool(data.get("force", False))

    if source not in AVAILABLE_SOURCES:
        return jsonify(
            {
                "success": False,
                "error": f"Repeat crawl is not supported for source: {source}",
                "available_sources": AVAILABLE_SOURCES,
            }
        ), 400

    tiktok_config = None
    worker_target = crawl_worker
    worker_args = None
    crawl_mode = "interval"
    start_message = None
    active_config = None
    next_run_at = None

    if source == "tiktok":
        tiktok_config, error = build_tiktok_repeat_config(data)
        if error:
            return jsonify({"success": False, "error": error}), 400

        worker_target = crawl_tiktok_schedule_worker
        worker_args = (tiktok_config,)
        crawl_mode = tiktok_config["schedule_type"]
        active_config = {
            "schedule_type": tiktok_config["schedule_type"],
            "input_mode": tiktok_config["input_mode"],
            "input_values": list(tiktok_config["input_values"]),
            "limit": tiktok_config["limit"],
            "save_mode": tiktok_config["save_mode"],
            "profile_mode": tiktok_config["profile_mode"],
            "schedule": tiktok_config["schedule"],
            "test_schedule": tiktok_config["test_schedule"],
        }
        if tiktok_config["schedule_type"] == "minute_test":
            next_run_at = get_next_daily_time_slot_run_at(
                tiktok_config["test_schedule"]["slots"],
                kst_now(),
            )
        else:
            next_run_at = get_next_tiktok_run_at(tiktok_config["schedule"], kst_now())
        if not next_run_at:
            return jsonify({"success": False, "error": "No upcoming TikTok batch slot is available."}), 400

        if tiktok_config["schedule_type"] == "minute_test":
            start_message = f"TikTok minute test crawl armed. Waiting for next KST slot at {fmt_kst(next_run_at)}"
        else:
            start_message = f"TikTok batch crawl armed. Waiting for next KST slot at {fmt_kst(next_run_at)}"
    else:
        if source not in FETCHER_IMPORTS:
            return jsonify(
                {
                    "success": False,
                    "error": f"Repeat crawl is not supported for source: {source}",
                    "available_sources": list(FETCHER_IMPORTS.keys()),
                }
            ), 400

        if interval_seconds <= 0:
            return jsonify({"success": False, "error": "interval_seconds must be >= 1."}), 400

        worker_args = (source, interval_seconds)
        start_message = f"{source} repeat crawl started"

    with STATE_LOCK:
        if CRAWL_STATE["is_running"]:
            return jsonify(
                {
                    "success": False,
                    "error": "A crawl is already running.",
                    "running_source": CRAWL_STATE["source"],
                }
            ), 409

        last_completed = CRAWL_STATE["last_completed_at"].get(source)
        if source != "tiktok" and last_completed and (utc_now() - last_completed) < timedelta(hours=24) and not force:
            return jsonify(
                {
                    "success": False,
                    "require_confirm": True,
                    "message": f"{source} ran within the last 24 hours. Start again?",
                    "last_completed_at": fmt_utc(last_completed),
                    "last_result_count": CRAWL_STATE["last_result_count"].get(source, 0),
                }
            ), 409

        stop_event = threading.Event()
        worker = threading.Thread(
            target=worker_target,
            args=(*worker_args, stop_event),
            daemon=True,
        )

        CRAWL_STATE["is_running"] = True
        CRAWL_STATE["source"] = source
        CRAWL_STATE["mode"] = crawl_mode
        CRAWL_STATE["interval_seconds"] = interval_seconds if source != "tiktok" else None
        CRAWL_STATE["next_run_at"] = next_run_at
        CRAWL_STATE["last_started_at"] = utc_now()
        CRAWL_STATE["active_config"] = active_config
        CRAWL_STATE["last_schedule_slot"] = None
        CRAWL_STATE["execution_count"] = 0
        CRAWL_STATE["executed_schedule_slots"] = []
        CRAWL_STATE["stop_event"] = stop_event
        CRAWL_STATE["thread"] = worker

        worker.start()

    return jsonify(
        {
            "success": True,
            "message": start_message,
            "source": source,
            "interval_seconds": interval_seconds if source != "tiktok" else None,
            "mode": crawl_mode,
            "next_run_at_kst": fmt_kst(next_run_at),
        }
    )


@app.route("/crawl/stop", methods=["POST"])
def crawl_stop():
    with STATE_LOCK:
        if not CRAWL_STATE["is_running"] or not CRAWL_STATE["stop_event"]:
            return jsonify({"success": False, "error": "No active repeat crawl."}), 400

        CRAWL_STATE["stop_event"].set()
        source = CRAWL_STATE["source"]

    add_http_log(f"[NOTICE] [CRAWL] stop requested / source={source}")

    return jsonify({"success": True, "message": f"{source} crawl stop requested"})


@app.route("/fetch", methods=["GET"])
def fetch():
    source = request.args.get("source", "").strip().lower()

    if not source:
        return jsonify(
            {
                "success": False,
                "error": "source query parameter is required.",
                "example": "/fetch?source=naver",
                "available_sources": AVAILABLE_SOURCES,
            }
        ), 400

    if source not in FETCHER_IMPORTS:
        return jsonify(
            {
                "success": False,
                "error": f"Single fetch is not supported for source: {source}",
                "available_sources": AVAILABLE_SOURCES,
            }
        ), 400

    try:
        products = run_fetch_once(source)
        return jsonify({"success": True, "source": source, "count": len(products), "products": products})
    except Exception as exc:
        update_result_state(source, 0, error=str(exc))
        add_http_log(f"[ERROR] [FETCH] {source} single fetch failed: {exc}")
        return jsonify({"success": False, "source": source, "error": str(exc)}), 500


@app.route("/fetch/tiktok/auto", methods=["POST"])
def fetch_tiktok_auto():
    data = request.get_json(silent=True) or {}
    input_mode = str(data.get("input_mode", "keyword")).strip().lower() or "keyword"
    save_mode = str(data.get("save_mode", "overwrite")).strip().lower() or "overwrite"
    profile_mode = str(data.get("profile_mode", "default")).strip().lower() or "default"
    input_value = str(
        data.get("input_value")
        or data.get("keyword")
        or data.get("video_url")
        or ""
    ).strip()
    limit = parse_positive_int(data.get("limit", 10), 10)

    if input_mode not in {"keyword", "url"}:
        return jsonify({"success": False, "error": "input_mode must be 'keyword' or 'url'."}), 400

    if save_mode not in {"overwrite", "append"}:
        return jsonify({"success": False, "error": "save_mode must be 'overwrite' or 'append'."}), 400

    if profile_mode not in {"default", "automation"}:
        return jsonify({"success": False, "error": "profile_mode must be 'default' or 'automation'."}), 400

    if not input_value:
        field_label = "keyword" if input_mode == "keyword" else "video_url"
        return jsonify({"success": False, "error": f"{field_label} is required for TikTok auto fetch."}), 400

    add_http_log(
        f"[NOTICE] [FETCH] tiktok auto fetch started / mode={input_mode} / input={input_value} / save_target={limit} / save_mode={save_mode} / profile_mode={profile_mode}"
    )

    try:
        result = invoke_tiktok_auto_fetch(
            input_mode=input_mode,
            input_value=input_value,
            limit=limit,
            save_mode=save_mode,
            profile_mode=profile_mode,
        )
    except ModuleNotFoundError as exc:
        update_result_state(
            "tiktok",
            0,
            error=str(exc),
            details={"input_value": input_value, "input_mode": input_mode, "save_mode": save_mode, "profile_mode": profile_mode, "limit": limit, "mode": "auto"},
        )
        add_http_log(f"[ERROR] [FETCH] tiktok dependency missing: {exc}")
        return jsonify(
            {
                "success": False,
                "source": "tiktok_auto",
                "error": f"Missing dependency: {exc}",
            }
        ), 500
    except Exception as exc:
        update_result_state(
            "tiktok",
            0,
            error=str(exc),
            details={"input_value": input_value, "input_mode": input_mode, "save_mode": save_mode, "profile_mode": profile_mode, "limit": limit, "mode": "auto"},
        )
        add_http_log(f"[ERROR] [FETCH] tiktok auto fetch failed: {exc}")
        return jsonify({"success": False, "source": "tiktok_auto", "error": str(exc)}), 500

    details = build_tiktok_fetch_details(
        result=result,
        profile_mode=profile_mode,
        limit=limit,
        trigger="manual",
    )
    update_result_state(
        "tiktok",
        len(result["products"]),
        error=result["blocked_reason"] or result["save_error"],
        details=details,
    )
    log_tiktok_fetch_result(result)

    if result["blocked_reason"]:
        return jsonify(
            {
                "success": False,
                "source": "tiktok_auto",
                "keyword": result["keyword"],
                "input_value": result["input_value"],
                "input_mode": result["input_mode"],
                "save_mode": result["save_mode"],
                "profile_mode": result.get("profile_mode", profile_mode),
                "save_target_count": result["save_target_count"],
                "discovered_count": result["discovered_count"],
                "saved_count": result["saved_count"],
                "products": result["products"],
                "error": result["blocked_reason"],
                "save_error": result["save_error"],
                "login_state": result.get("login_state"),
                "login_state_before": result.get("login_state_before"),
                "login_state_reason": result.get("login_state_reason"),
            }
        ), 409

    return jsonify(
        {
            "success": True,
            "source": "tiktok_auto",
            "keyword": result["keyword"],
            "input_value": result["input_value"],
            "input_mode": result["input_mode"],
            "save_mode": result["save_mode"],
            "profile_mode": result.get("profile_mode", profile_mode),
            "save_target_count": result["save_target_count"],
            "discovery_limit": result["discovery_limit"],
            "count": len(result["products"]),
            "discovered_count": result["discovered_count"],
            "saved_count": result["saved_count"],
            "sheet_name": result["sheet_name"],
            "products": result["products"],
            "save_error": result["save_error"],
            "login_state": result.get("login_state"),
            "login_state_before": result.get("login_state_before"),
            "login_state_reason": result.get("login_state_reason"),
        }
    )


def open_dashboard_in_chrome(browser_url: str):
    time.sleep(1.5)

    if os.path.exists(CHROME_PATH):
        try:
            subprocess.Popen([CHROME_PATH, browser_url])
            print(f"Dashboard opened in Chrome: {browser_url}")
        except Exception as exc:
            print(f"Chrome launch failed: {exc}")
    else:
        print(f"Chrome executable not found: {CHROME_PATH}")


if __name__ == "__main__":
    runtime_settings = get_runtime_settings()
    should_open_browser = runtime_settings["open_browser"] and (
        not runtime_settings["use_reloader"] or os.environ.get("WERKZEUG_RUN_MAIN") == "true"
    )
    if should_open_browser:
        threading.Thread(
            target=open_dashboard_in_chrome,
            args=(runtime_settings["browser_url"],),
            daemon=True,
        ).start()

    app.run(
        host=runtime_settings["host"],
        port=runtime_settings["port"],
        debug=runtime_settings["debug"],
        use_reloader=runtime_settings["use_reloader"],
    )
