# Sprint 1: Client-Side API Contract Mapper & OpenAPI 3.0 Generator

## Objective
Implement a production-grade, modular Python tool that extracts client-side JavaScript bundles, discovers undocumented or internal API endpoints and data schemas, synthesizes them into OpenAPI 3.0 specifications via Anthropic Claude Opus 5.5, and exports documentation catalogs.

## Required Modules to Implement

### 1. `config.py`
- Load `.env` using `python-dotenv`.
- Manage configuration settings:
  - `ANTHROPIC_API_KEY`: API key.
  - `ANTHROPIC_MODEL`: Model name (default `claude-3-opus-20240229`).
  - `OUTPUT_DIR`: Defaults to `output/`.
  - `MAX_SCRIPTS`: Max scripts to download per target (default 20).
  - `REQUEST_TIMEOUT`: Timeout in seconds (default 15).

### 2. `fetcher.py`
- Uses `requests` with standard browser headers (`User-Agent`, `Accept`, `Accept-Language`).
- Function `fetch_html(url: str) -> str`: Fetches page content.
- Function `extract_script_urls(html: str, base_url: str) -> list[str]`: Parses all `<script src="...">` tags, resolves relative URLs to absolute. Filters out third-party trackers (Google Analytics, Facebook Pixel, Yandex Metrika, Sentry, Hotjar).
- Function `download_and_format_script(url: str, output_dir: Path) -> Path`: Downloads script, formats using `jsbeautifier`, saves to `output_dir`.

### 3. `extractor.py`
- Static pattern extraction without executing code:
  - `extract_endpoints(content: str) -> list[dict]`: Heuristic extraction of API routes (`/api/.*`, `/v[1-9]/.*`, `/graphql.*`, `/rest/.*`, `/internal/.*`). Identifies associated HTTP methods (GET, POST, PUT, DELETE, PATCH).
  - `extract_state_models(content: str) -> list[str]`: Identifies object keys matching client-side state models, mutations, and payload properties.
  - `extract_candidate_code_blocks(content: str, max_chars_per_block: int = 4000) -> list[str]`: Extracts functions or object declarations containing API client calls (e.g. `axios`, `fetch`, `useQuery`, `useMutation`).

### 4. `schema_builder.py`
- Integrates with Anthropic Python SDK via `anthropic.Anthropic`.
- Formulates a clean developer-oriented system prompt for OpenAPI specification synthesis:
  ```
  You are an automated API architecture assistant and OpenAPI 3.0 synthesizer.
  Analyze the provided client-side JavaScript endpoint declarations, request structures, and data models.
  Synthesize a compliant OpenAPI 3.0 path object catalog including:
  1. Full endpoint paths and normalized HTTP methods.
  2. Query parameters, path parameters, and header expectations.
  3. Reconstructed request body schemas and field types (string, integer, boolean, object).
  4. Probable response status codes and schema structures.
  Return your response STRICTLY as valid JSON matching OpenAPI 3.0 path structures.
  ```
- Parses structured JSON response from Claude and handles API limits gracefully.

### 5. `exporter.py`
- Formats and writes:
  - `openapi.json`: Valid OpenAPI 3.0 specification file.
  - `openapi.yaml`: YAML version of the specification.
  - `api_catalog.md`: Markdown summary table with all routes, methods, required parameters, and data models.

### 6. `main.py`
- CLI application using `argparse` and `rich.console`.
- Commands:
  - `--url <TARGET_URL>`: Fetch target, discover scripts, beautify, extract, build OpenAPI schema, and export.
  - `--file <LOCAL_JS_PATH>`: Process a single local JS file directly.
  - `--dry-run`: Run fetching and regex extraction without calling Anthropic API (for testing without token cost).
- Displays clean status tables and progress indicators.

## Rules & Constraints
1. Language: English only for all code, names, error messages, and CLI outputs.
2. NO COMMENTS in code: Zero `#`, `//`, or `/* */`. Code must be concise, expressive, and clean.
3. Clean error handling: Catch network failures, JSON decode errors, and API limits with clear console notifications.
