from __future__ import annotations

import importlib.util
import os
import sys
import threading
from pathlib import Path

from flask import render_template
from jinja2 import ChoiceLoader, FileSystemLoader

HB_ROOT = Path(__file__).resolve().parent
LIVE_ROOT = HB_ROOT.parent
LIVE_APP_PATH = LIVE_ROOT / "app.py"
BROWSER_PROFILE_ROOT = HB_ROOT / "crawler" / "browser_profile"
LIVE_APP_MODULE_NAME = "mysellcomb_live_app"
HB_TEMPLATE_ROOT = HB_ROOT / "templates"


def _configure_default_env() -> None:
    BROWSER_PROFILE_ROOT.mkdir(parents=True, exist_ok=True)

    os.environ.setdefault("MYSELLCOMB_HOST", "127.0.0.1")
    os.environ.setdefault("MYSELLCOMB_PORT", "5010")
    os.environ.setdefault("MYSELLCOMB_DEBUG", "0")
    os.environ.setdefault("MYSELLCOMB_USE_RELOADER", "0")
    os.environ.setdefault("MYSELLCOMB_OPEN_BROWSER", "0")

    port = (os.environ.get("MYSELLCOMB_PORT") or "5010").strip() or "5010"
    os.environ.setdefault("MYSELLCOMB_BROWSER_URL", f"http://127.0.0.1:{port}/")
    os.environ.setdefault("MYSELLCOMB_BROWSER_PROFILE_ROOT", str(BROWSER_PROFILE_ROOT))
    os.environ.setdefault("MYSELLCOMB_DATA_ROOT", str(HB_ROOT / "data"))
    os.environ.setdefault("TIKTOK_WORKSHEET_NAME", "TikTok_Hb")

    credentials_path = LIVE_ROOT / "credentials.json"
    if credentials_path.exists():
        os.environ.setdefault("GOOGLE_SHEET_CREDENTIALS", str(credentials_path))


def _load_live_app_module():
    if LIVE_APP_MODULE_NAME in sys.modules:
        return sys.modules[LIVE_APP_MODULE_NAME]

    if not LIVE_APP_PATH.exists():
        raise FileNotFoundError(f"Live app entrypoint was not found: {LIVE_APP_PATH}")

    live_root_str = str(LIVE_ROOT)
    if live_root_str not in sys.path:
        sys.path.insert(0, live_root_str)

    spec = importlib.util.spec_from_file_location(LIVE_APP_MODULE_NAME, LIVE_APP_PATH)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Failed to load module spec for {LIVE_APP_PATH}")

    module = importlib.util.module_from_spec(spec)
    sys.modules[LIVE_APP_MODULE_NAME] = module
    spec.loader.exec_module(module)
    return module


_configure_default_env()
LIVE_APP = _load_live_app_module()
app = LIVE_APP.app
app.jinja_loader = ChoiceLoader([
    FileSystemLoader(str(HB_TEMPLATE_ROOT)),
    app.jinja_loader,
])
app.config["MYSELLCOMB_IS_HB"] = True
app.config["MYSELLCOMB_HB_TIKTOK_TEST_URL"] = "/hb/tiktok-schedule-test"


@app.get("/hb/tiktok-schedule-test")
def hb_tiktok_schedule_test():
    return render_template("hb_tiktok_schedule_test.html")


if __name__ == "__main__":
    runtime_settings = LIVE_APP.get_runtime_settings()
    should_open_browser = runtime_settings["open_browser"] and (
        not runtime_settings["use_reloader"] or os.environ.get("WERKZEUG_RUN_MAIN") == "true"
    )
    if should_open_browser:
        threading.Thread(
            target=LIVE_APP.open_dashboard_in_chrome,
            args=(runtime_settings["browser_url"],),
            daemon=True,
        ).start()

    app.run(
        host=runtime_settings["host"],
        port=runtime_settings["port"],
        debug=runtime_settings["debug"],
        use_reloader=runtime_settings["use_reloader"],
    )
