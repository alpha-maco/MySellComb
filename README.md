# MySellComb

MySellComb keeps the production runtime and the Hb validation runtime separate.

## Roles

- `MySellComb`
  - Live server
  - Default port `5000`
  - Production TikTok cycle entrypoint: [run_tiktok_keyword_cycle.py](/E:/DevOps/python/MySellComb/run_tiktok_keyword_cycle.py)

- `MySellComb_Hb`
  - Hb서버 for heartbeat, recovery, and validation work
  - Default port `5010`
  - Hb wrapper entrypoint: [run_tiktok_keyword_cycle_if_due.py](/E:/DevOps/python/MySellComb/MySellComb_Hb/run_tiktok_keyword_cycle_if_due.py)

## Operating Rules

- Terminology:
  - Production runtime is `Live`
  - Heartbeat and validation runtime is `Hb서버`

- Deployment flow:
  - Apply changes to `Hb서버` first
  - Validate the behavior there
  - Hot deploy the confirmed change to `Live`

The Live project keeps [run_tiktok_keyword_cycle_if_due.py](/E:/DevOps/python/MySellComb/run_tiktok_keyword_cycle_if_due.py) only as a shim that points to the Hb서버 entrypoint.

## Main Features

- TikTok auto-fetch workflow
- Search-based product filtering
- Google Spreadsheet save integration
- Flask dashboard UI
- Repeat crawl scheduling
