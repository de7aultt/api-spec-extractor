# Sprint 1: Bug Bounty JS Recon & Static Security Audit Pipeline

## Objective
Implement a production-grade, modular Python reconnaissance and static code analysis tool for Bug Bounty scopes.
The tool extracts client-side JavaScript bundles, parses endpoints and sensitive state parameters, sends candidate structures to Opus 5.5 for semantic SAST auditing, and generates a structured HackerOne triage report.

## Required Files to Implement

### 1. `config.py`
- Load `.env` with `python-dotenv`.
- Manage configuration variables:
  - `ANTHROPIC_API_KEY`: API key.
  - `ANTHROPIC_MODEL`: Model name (default `claude-3-5-sonnet-20241022` or `claude-3-opus-20240229`).
  - `OUTPUT_DIR`: Defaults to `reports/`.
  - `MAX_SCRIPTS`: Max scripts to download per target (default 20).
  - `REQUEST_TIMEOUT`: Timeout in seconds (default 15).

### 2. `fetcher.py`
- Uses `requests` with realistic browser headers (`User-Agent`, `Accept`, `Accept-Language`).
- Function `fetch_html(url: str) -> str`: Fetches page content.
- Function `extract_script_urls(html: str, base_url: str) -> list[str]`: Uses BeautifulSoup to find all `<script src="...">` tags, resolves relative URLs to absolute. Filters out third-party trackers (Google Analytics, Facebook Pixel, Yandex Metrika, Sentry, Hotjar).
- Function `download_and_format_script(url: str, output_dir: Path) -> Path`: Downloads the script, beautifies with `jsbeautifier`, saves to `output_dir`.

### 3. `extractor.py`
- Static analysis heuristics without executing code:
  - `extract_endpoints(content: str) -> list[dict]`: Regex and heuristic extraction of API routes (`/api/.*`, `/v[1-9]/.*`, `/graphql.*`, `/oauth/.*`, `/internal/.*`). Identifies associated HTTP methods if adjacent.
  - `extract_state_models(content: str) -> list[str]`: Identifies object keys matching sensitive field patterns (`role`, `isAdmin`, `is_admin`, `superadmin`, `permissions`, `balance`, `credit`, `token`, `secret`, `apiKey`, `tenant_id`, `org_id`).
  - `extract_candidate_code_blocks(content: str, max_chars_per_block: int = 4000) -> list[str]`: Locates functions or object declarations containing sensitive fields or API calls for AI review.

### 4. `auditor.py`
- Communicates with Anthropic API via `anthropic.Anthropic`.
- Formulates strict SAST / Defensive Code Review system prompt:
  ```
  You are an automated Static Application Security Testing (SAST) auditor.
  Analyze the provided client-side JavaScript endpoints, state models, and code blocks extracted from an authorized Bug Bounty scope.
  Perform defensive evaluation to catalog:
  1. Undocumented or internal API routes.
  2. Potential Mass Assignment fields (parameters in client state that should not be accepted by server-side updates).
  3. Insecure Direct Object Reference (IDOR) candidates (endpoints relying directly on user-controllable object identifiers).
  4. Sensitive data exposure in client bundles.
  Return your response STRICTLY as valid JSON matching the requested schema.
  ```
- Parses structured JSON response from Claude and handles API errors gracefully.

### 5. `reporter.py`
- Formats findings into a clean Markdown document matching HackerOne / Bugcrowd triage templates:
  - Target URL & Scan Metadata.
  - Executive Risk Summary.
  - API Surface Catalog (endpoints, methods, parameters).
  - Suspected Security Issues (Mass Assignment, IDOR candidate endpoints, exposed secrets).
  - Verification & Triage Next Steps (example `curl` commands for authorized reproduction).

### 6. `main.py`
- CLI application using `argparse` and `rich.console`.
- Supports:
  - `--url <TARGET_URL>`: Fetch target, discover scripts, beautify, extract, audit, and report.
  - `--file <LOCAL_JS_PATH>`: Audit a single local JS file directly.
  - `--dry-run`: Run fetching and regex extraction without calling Anthropic API (saves tokens).
- Displays clean status tables and progress indicators.

## Rules & Constraints
1. Language: English only for all code, names, error messages, and CLI outputs.
2. NO COMMENTS in code: Zero `#`, `//`, or `/* */`. Code must be concise, expressive, and clean.
3. Clean error handling: Catch network failures, JSON decode errors, and API quota limits with clear console notifications.
