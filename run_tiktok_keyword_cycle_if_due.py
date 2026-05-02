from __future__ import annotations

import sys
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parent
HB_SCRIPT_PATH = PROJECT_ROOT / "MySellComb_Hb" / "run_tiktok_keyword_cycle_if_due.py"


def main() -> int:
    message = (
        "Heartbeat wrapper has moved out of Live.\n"
        f"Use the Hb project entrypoint instead: {HB_SCRIPT_PATH}"
    )
    print(message, file=sys.stderr)
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
