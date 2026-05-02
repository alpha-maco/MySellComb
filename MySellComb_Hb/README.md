# MySellComb_Hb

`MySellComb_Hb` is the separate Hb서버 for validating changes before they reach Live.

## Roles

- `MySellComb`
  - Live server
  - Default port `5000`
  - Production cycle entrypoint: [run_tiktok_keyword_cycle.py](/E:/DevOps/python/MySellComb/run_tiktok_keyword_cycle.py)

- `MySellComb_Hb`
  - Hb서버
  - Default port `5010`
  - Hb wrapper entrypoint: [run_tiktok_keyword_cycle_if_due.py](/E:/DevOps/python/MySellComb/MySellComb_Hb/run_tiktok_keyword_cycle_if_due.py)

## Hb서버 Defaults

- No top-of-hour restriction
- Runs immediately when invoked
- TikTok save target count `2`
- TikTok worksheet name `TikTok_Hb`

This keeps Hb서버 behavior independent from the Live schedule and makes it suitable for recovery and validation runs.

## Separation Rules

- Port split:
  - Live `5000`
  - Hb서버 `5010`

- Browser profile split:
  - Live `E:\DevOps\python\MySellComb\crawler\browser_profile`
  - Hb서버 `E:\DevOps\python\MySellComb\MySellComb_Hb\crawler\browser_profile`

- Log split:
  - Live `live_server_stdout.log`, `live_server_stderr.log`
  - Hb서버 `hb_server_stdout.log`, `hb_server_stderr.log`

- State file split:
  - Hb wrapper state `data/tiktok_keyword_cycle_state.json`

## Entrypoints

- Hb서버 startup:
  - [start_dashboard.cmd](/E:/DevOps/python/MySellComb/MySellComb_Hb/start_dashboard.cmd)
  - [launch_dashboard.py](/E:/DevOps/python/MySellComb/MySellComb_Hb/launch_dashboard.py)

- Hb서버 wrapper:
  - [run_tiktok_keyword_cycle_if_due.py](/E:/DevOps/python/MySellComb/MySellComb_Hb/run_tiktok_keyword_cycle_if_due.py)

- Hb서버 TikTok cycle:
  - [run_tiktok_keyword_cycle.py](/E:/DevOps/python/MySellComb/MySellComb_Hb/run_tiktok_keyword_cycle.py)
