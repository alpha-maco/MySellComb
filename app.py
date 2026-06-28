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
DASHBOARD_URL = "http://127.0.0.1:5000/"

HTTP_LOGS = []
MAX_LOGS = 300

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


def add_http_log(message: str):
    timestamp = fmt_utc(utc_now())
    HTTP_LOGS.append(f"[{timestamp}] {message}")
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
        f'{("?" + request.query_string.decode()) if request.query_string else ""}" {response.status_code}'
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


@app.route("/")
def index():
    return render_template("index.html", products=[])


@app.route("/health")
def health():
    return jsonify({"status": "ok", "available_sources": AVAILABLE_SOURCES})


@app.route("/logs")
def logs():
    return jsonify({"success": True, "logs": HTTP_LOGS[-120:]})


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
    keyword = str(data.get("keyword", "")).strip()
    limit = parse_positive_int(data.get("limit", 10), 10)

    if not keyword:
        return jsonify({"success": False, "error": "keyword is required for TikTok auto fetch."}), 400

    add_http_log(f"[NOTICE] [FETCH] tiktok auto fetch started / keyword={keyword} / limit={limit}")

    try:
        tiktok_auto_fetch = getattr(import_module("crawler.tiktok_fetcher"), "fetch_auto")
        result = tiktok_auto_fetch(keyword=keyword, limit=limit)
    except ModuleNotFoundError as exc:
        update_result_state(
            "tiktok",
            0,
            error=str(exc),
            details={"keyword": keyword, "limit": limit, "mode": "auto"},
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
            details={"keyword": keyword, "limit": limit, "mode": "auto"},
        )
        add_http_log(f"[ERROR] [FETCH] tiktok auto fetch failed: {exc}")
        return jsonify({"success": False, "source": "tiktok_auto", "error": str(exc)}), 500

    details = {
        "mode": "auto",
        "keyword": keyword,
        "limit": limit,
        "discovered_count": result["discovered_count"],
        "saved_count": result["saved_count"],
        "sheet_name": result["sheet_name"],
        "save_error": result["save_error"],
        "blocked_reason": result["blocked_reason"],
    }
    update_result_state(
        "tiktok",
        len(result["products"]),
        error=result["blocked_reason"] or result["save_error"],
        details=details,
    )

    if result["blocked_reason"]:
        add_http_log(
            "[ERROR] [FETCH] "
            f"tiktok blocked / keyword={keyword} / discovered={result['discovered_count']} / "
            f"saved={result['saved_count']} / reason={result['blocked_reason']}"
        )
        return jsonify(
            {
                "success": False,
                "source": "tiktok_auto",
                "keyword": keyword,
                "discovered_count": result["discovered_count"],
                "saved_count": result["saved_count"],
                "products": result["products"],
                "error": result["blocked_reason"],
                "save_error": result["save_error"],
            }
        ), 409

    add_http_log(
        "[CRAWL] "
        f"tiktok auto fetch finished / keyword={keyword} / discovered={result['discovered_count']} / "
        f"saved={result['saved_count']}"
    )
    if result["save_error"]:
        add_http_log(f"[ERROR] [SAVE] tiktok sheet save failed / keyword={keyword} / {result['save_error']}")

    return jsonify(
        {
            "success": True,
            "source": "tiktok_auto",
            "keyword": keyword,
            "count": len(result["products"]),
            "discovered_count": result["discovered_count"],
            "saved_count": result["saved_count"],
            "sheet_name": result["sheet_name"],
            "products": result["products"],
            "save_error": result["save_error"],
        }
    )


def open_dashboard_in_chrome():
    time.sleep(1.5)

    if os.path.exists(CHROME_PATH):
        try:
            subprocess.Popen([CHROME_PATH, DASHBOARD_URL])
            print(f"Dashboard opened in Chrome: {DASHBOARD_URL}")
        except Exception as exc:
            print(f"Chrome launch failed: {exc}")
    else:
        print(f"Chrome executable not found: {CHROME_PATH}")


if __name__ == "__main__":
    if os.environ.get("WERKZEUG_RUN_MAIN") == "true":
        threading.Thread(target=open_dashboard_in_chrome, daemon=True).start()

    app.run(debug=True)
