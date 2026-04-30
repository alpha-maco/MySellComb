from __future__ import annotations

import json
import subprocess
import sys
from datetime import datetime
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parent
STATE_PATH = PROJECT_ROOT / "data" / "tiktok_keyword_cycle_state.json"
WINDOW_DATES = {"2026-04-30", "2026-05-01"}


def load_state() -> dict:
    if not STATE_PATH.exists():
        return {}
    try:
        return json.loads(STATE_PATH.read_text(encoding="utf-8"))
    except Exception:
        return {}


def save_state(state: dict) -> None:
    STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
    STATE_PATH.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")


def main() -> int:
    force_run = "--force" in sys.argv[1:]
    now = datetime.now()
    date_key = now.strftime("%Y-%m-%d")
    hour_key = now.strftime("%Y-%m-%d %H")
    state = load_state()

    if not force_run:
        if date_key not in WINDOW_DATES:
            print(f"SKIP window_closed date={date_key}")
            return 0

        if now.minute != 0:
            print(f"SKIP not_top_of_hour now={now.strftime('%Y-%m-%d %H:%M:%S')}")
            return 0

        if state.get("last_attempt_hour") == hour_key:
            print(f"SKIP already_ran hour={hour_key}")
            return 0

    result = subprocess.run(
        [sys.executable, str(PROJECT_ROOT / "run_tiktok_keyword_cycle.py")],
        cwd=PROJECT_ROOT,
        check=False,
    )

    state["last_attempt_hour"] = hour_key
    state["last_attempt_at"] = now.strftime("%Y-%m-%d %H:%M:%S")
    state["last_return_code"] = result.returncode
    save_state(state)

    if result.returncode == 0:
        print(f"RUN success hour={hour_key}")
        return 0

    print(f"RUN failed hour={hour_key} code={result.returncode}")
    return result.returncode


if __name__ == "__main__":
    raise SystemExit(main())
