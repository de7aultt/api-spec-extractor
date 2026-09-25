# Sprint 3: Bug Bounty Target Radar & Web Dashboard

## Objective
Evolve `api-spec-extractor` into a unified local command center:
1. **HackerOne Target Radar**: Automated fetcher, cache, and filter engine for active HackerOne bounty programs.
2. **Interactive Web Dashboard**: Modern Flask web application with a dark theme displaying the radar feed, in-scope web assets, a 1-click scan launcher, and an embedded Swagger UI viewer for generated OpenAPI specifications.

## Architecture & Required Modules

### 1. `radar.py` (HackerOne Target Aggregator)
- Source feed URL:
  `https://raw.githubusercontent.com/arkadiyt/bounty-targets-data/master/data/hackerone_data.json`
- Function `fetch_or_load_targets(force_refresh: bool = False, cache_path: Path = Path("data/hackerone_targets.json")) -> list[dict]`:
  - If cache exists and is less than 24 hours old (and not `force_refresh`), load from local disk.
  - Otherwise, download feed with a timeout, save to `cache_path`, and return parsed JSON.
- Function `filter_targets(programs: list[dict], min_bounty: float = 0, web_only: bool = True, search_query: str = "") -> list[dict]`:
  - Filters strictly for `offers_bounties == True` (excludes unpaid VDP).
  - When `web_only` is true, extracts only in-scope assets where `asset_type in ["URL", "WILDCARD"]`.
  - Filters out out-of-scope assets.
  - Matches program name or handle against `search_query`.
  - Returns structured list of programs with:
    - `name`, `handle`, `url` (HackerOne link).
    - `bounty_min`, `bounty_max`, `average_bounty`.
    - `in_scope_domains`: list of clean target URLs/domains.

### 2. `server.py` (Flask Web Dashboard)
- Framework: Flask + threading/concurrent worker for background extraction jobs.
- Endpoints:
  - `GET /`: Main dashboard SPA.
  - `GET /api/targets`: Returns JSON list of filtered HackerOne targets (accepts `search`, `bounty_min`, `refresh`).
  - `POST /api/scan`: Accepts JSON `{"url": "https://target.com"}`. Spawns an asynchronous extraction task running `main.py` pipeline. Returns `{"job_id": "...", "status": "running"}`.
  - `GET /api/scan/<job_id>`: Returns status (`running`, `completed`, `failed`), logs, and output file locations.
  - `GET /api/openapi/<job_id>`: Serves the generated `openapi.json` for Swagger UI.
  - `GET /swagger/<job_id>`: Serves a Swagger UI page pointing to `/api/openapi/<job_id>`.
  - `GET /download/<job_id>/<file_type>`: Serves `openapi.json`, `openapi.yaml`, or `api_catalog.md`.

### 3. Web UI Assets (`templates/` and `static/`)
- Single Page Application with clean dark theme (Tailwind CSS via CDN, modern typography, Lucide icons via CDN).
- **Tab 1: Target Radar**:
  - Search bar + bounty filters.
  - Refresh Feed button.
  - Table of programs with tags, bounty badges, and list of in-scope domains.
  - Action button on each domain: `[⚡ Scan API]`, which switches to the Scanner tab and begins extraction.
- **Tab 2: Recon Jobs**:
  - Live list of past and running scans with target URL, date, status, and link to Swagger UI.
  - Manual scan input box (enter any custom URL).
- **Tab 3: Swagger UI / Spec Viewer**:
  - Embedded Swagger UI (via `swagger-ui-dist` CDN) showing live interactive API documentation of the selected scan.

### 4. CLI & Launcher Integration
- Add `--serve` flag to `main.py` (or execute `python server.py`).
- Create clean `run_dashboard.bat`:
  ```bat
  @echo off
  cd /d "%~dp0"
  python server.py
  pause
  ```

## Rules & Constraints
1. English only across all code, HTML, UI text, logs, and schemas.
2. NO CODE COMMENTS (`#`, `//`, `/* */`).
3. Clean error handling: If HackerOne feed fails to download or network is offline, load fallback cached data or display clear UI error.
