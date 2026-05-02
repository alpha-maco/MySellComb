import os
import subprocess
import threading
import time
from datetime import datetime, timedelta, timezone
from importlib import import_module

import bootstrap
from flask import Flask, jsonify, render_template, request

app = Flask(__name__)

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

CRAWL_STATE = {
    "is_running": False,
    "source": None,
    "interval_seconds": None,
    "last_started_at": None,
    "last_stopped_at": None,
    "last_completed_at": {},
    "last_result_count": {},
    "last_error": {},
    "last_details": {},
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
        with STATE_LOCK:
            CRAWL_STATE["is_running"] = False
            CRAWL_STATE["source"] = None
            CRAWL_STATE["interval_seconds"] = None
            CRAWL_STATE["thread"] = None
            CRAWL_STATE["stop_event"] = None
            CRAWL_STATE["last_stopped_at"] = utc_now()

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
            "interval_seconds": CRAWL_STATE["interval_seconds"],
            "last_started_at": fmt_utc(CRAWL_STATE["last_started_at"]),
            "last_stopped_at": fmt_utc(CRAWL_STATE["last_stopped_at"]),
            "last_completed_at": {
                key: fmt_utc(value) for key, value in CRAWL_STATE["last_completed_at"].items()
            },
            "last_result_count": CRAWL_STATE["last_result_count"],
            "last_error": CRAWL_STATE["last_error"],
            "last_details": CRAWL_STATE["last_details"],
        }
    return jsonify(payload)


@app.route("/crawl/start", methods=["POST"])
def crawl_start():
    data = request.get_json(silent=True) or {}

    source = str(data.get("source", "")).strip().lower()
    interval_seconds = parse_positive_int(data.get("interval_seconds", 0), 0)
    force = bool(data.get("force", False))

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
        if last_completed and (utc_now() - last_completed) < timedelta(hours=24) and not force:
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
            target=crawl_worker,
            args=(source, interval_seconds, stop_event),
            daemon=True,
        )

        CRAWL_STATE["is_running"] = True
        CRAWL_STATE["source"] = source
        CRAWL_STATE["interval_seconds"] = interval_seconds
        CRAWL_STATE["last_started_at"] = utc_now()
        CRAWL_STATE["stop_event"] = stop_event
        CRAWL_STATE["thread"] = worker

        worker.start()

    return jsonify(
        {
            "success": True,
            "message": f"{source} repeat crawl started",
            "source": source,
            "interval_seconds": interval_seconds,
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
        tiktok_auto_fetch = getattr(import_module("crawler.tiktok_fetcher"), "fetch_auto")
        result = tiktok_auto_fetch(
            query=input_value,
            limit=limit,
            input_mode=input_mode,
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

    details = {
        "mode": "auto",
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
    }
    update_result_state(
        "tiktok",
        len(result["products"]),
        error=result["blocked_reason"] or result["save_error"],
        details=details,
    )

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

    add_http_log(
        "[CRAWL] "
        f"tiktok auto fetch finished / mode={result['input_mode']} / input={result['input_value']} / discovered={result['discovered_count']} / save_mode={result['save_mode']} / "
        f"saved={result['saved_count']} / session={result.get('login_state', 'unknown')}"
    )
    if result["save_error"]:
        add_http_log(
            f"[ERROR] [SAVE] tiktok sheet save failed / mode={result['input_mode']} / input={result['input_value']} / {result['save_error']}"
        )

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
