# Sprint 2: Modern SPA Manifests & Framework Route Auto-Discovery

## Problem Statement
Modern Single Page Applications (SPAs) built with Vite, Next.js, and Laravel/Inertia do not declare all scripts in raw `<script src="...">` HTML tags.
For example:
- Vite stores its asset catalog in `/build/manifest.json` (often containing 100+ dynamically imported component chunks).
- Laravel + Inertia exposes complete backend routing tables inside inline HTML scripts via `const Ziggy = { "routes": { ... } }`.
Sprint 1 only extracted external `<script src="...">` tags and basic static strings, causing modern SPAs to report 0 endpoints despite having 100+ active routes and chunks.

## Objectives for Sprint 2

### 1. Upgrade `fetcher.py`: Manifest & Inline Asset Discovery
- **Manifest Detection**:
  - Automatically check for SPA manifests relative to target base URL:
    - `/build/manifest.json`
    - `/.vite/manifest.json`
    - `/manifest.json`
  - If a Vite manifest is found:
    - Parse JSON and extract all `.js` file paths from the manifest values (`file` attribute).
    - Queue discovered chunks for parallel downloading up to `MAX_SCRIPTS`.
- **Inline Script Extraction**:
  - Extract all inline `<script>` tags (scripts without `src` attribute) from the target HTML.
  - Save or bundle them as synthetic virtual scripts (e.g. `inline_scripts.js`) so that `extractor.py` inspects them.

### 2. Upgrade `extractor.py`: Framework Routing Engines
- **Laravel Ziggy Route Engine**:
  - Detect `Ziggy` configuration patterns in HTML and JS (e.g. `const Ziggy = {...}` or `window.Ziggy = {...}`).
  - Parse the JSON routing dictionary: extract all `uri` values, named routes, and allowed HTTP `methods` (GET, POST, PUT, DELETE, PATCH).
  - Normalize URIs to absolute API paths starting with `/`.
- **Inertia.js & Next.js Data Extractors**:
  - Parse `<div id="app" data-page="...">` or Next.js `__NEXT_DATA__` for initial page props, user states, and API endpoints.
- **Dynamic URL Pattern Enhancements**:
  - Match route patterns defined with Ziggy `route('name', ...)` calls and map them to their corresponding URIs.

### 3. Update `main.py`
- Display a dedicated summary table for "Framework & Manifest Discovery" showing:
  - Detected framework/bundler (e.g., "Vite (160 chunks in /build/manifest.json)", "Laravel Ziggy (101 routes in HTML)").
  - Total combined routes from manifests, inline scripts, and chunk files.

## Strict Rules
1. English only across all code, logs, and outputs.
2. NO CODE COMMENTS (`#`, `//`, `/* */`).
3. Maintain full backwards compatibility with existing CLI arguments (`--url`, `--file`, `--output`, `--dry-run`).
