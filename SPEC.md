# Architectural Specification: targetBugBounty

## Overview
`targetBugBounty` is a high-speed reconnaissance and static analysis tool designed for Bug Bounty hunters auditing client-side web applications (Next.js, Webpack, Vite, React, Vue).
It fetches public JavaScript bundles, formats them, performs AST/Regex extraction of routes and state variables, and passes candidate code chunks to Opus 5.5 for semantic vulnerability auditing (undocumented routes, mass assignment risks, exposed role checks, leaked keys).

## Core Pipeline

```
[Target URL / JS File]
        │
        ▼
[Fetcher Module (fetcher.py)]
  - Download scripts from HTML/URL
  - Extract sourcemaps if available
  - Beautify JS files
        │
        ▼
[Extractor Module (extractor.py)]
  - Extract API paths & endpoints (/api/*, /v1/*, GraphQL)
  - Extract Next.js page props (__NEXT_DATA__)
  - Extract potential hardcoded secrets & auth headers
        │
        ▼
[Opus Auditor (auditor.py)]
  - Defensive SAST prompt to Claude Opus 5.5
  - Identify mass-assignment parameters & hidden admin flags
  - Output structured JSON audit report
        │
        ▼
[Report Generator (reporter.py)]
  - Generate HackerOne/Bugcrowd markdown triage report
```

## Module Specifications

### 1. `config.py`
- Loads `.env` using `python-dotenv`.
- Stores `ANTHROPIC_API_KEY`, default model (`claude-3-opus-20240229` or latest Opus 5.5 alias), output directory paths.

### 2. `fetcher.py`
- Downloads HTML from target URL, parses `<script src="...">` tags.
- Downloads scripts asynchronously or concurrently with realistic browser headers.
- Formats minified JS using `jsbeautifier`.
- Saves raw and beautified files into `workspace/targets/<domain>/`.

### 3. `extractor.py`
- Regular expressions and parsing heuristics for endpoints, methods, parameters.
- Extracts JSON payloads, Next.js props, router routes.
- Filters out static assets (`.png`, `.svg`, `.css`, etc.).

### 4. `auditor.py`
- Integrates with Anthropic Python SDK.
- Sends chunks of extracted routes and controller code with a defensive SAST prompt.
- Receives JSON responses detailing:
  - Route catalog with HTTP methods.
  - Potential IDOR parameter candidates.
  - Suspect Mass Assignment fields (`role`, `isAdmin`, `status`, `balance`, etc.).
  - Hardcoded tokens or internal URLs.

### 5. `reporter.py`
- Takes the findings from `extractor.py` and `auditor.py`.
- Formats a standard Markdown report adhering to HackerOne / Bugcrowd submission guidelines.

### 6. `main.py`
- CLI entrypoint with `argparse`.
- Commands:
  - `--url <TARGET_URL>`: Run full recon pipeline on target.
  - `--file <LOCAL_JS>`: Audit local JavaScript file.
  - `--output <DIR>`: Specify output report path.
