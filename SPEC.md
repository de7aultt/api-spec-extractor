# Architectural Specification: api-spec-extractor

## Overview
`api-spec-extractor` is a high-performance developer tool designed to reverse-engineer client-side web bundles (Webpack, Vite, Next.js, Rollup) and produce clean, fully-typed OpenAPI 3.0 specifications and API route maps.

## Architecture

```
[Target URL / Local JS Files]
             │
             ▼
[Fetcher Module (fetcher.py)]
  - Download scripts and sourcemaps
  - Beautify minified JavaScript
             │
             ▼
[Extractor Module (extractor.py)]
  - Regex and AST pattern matching for API routes (/api/*, /v[1-9]/*, GraphQL)
  - Extract Next.js page props and client-side state models
  - Extract request methods and query parameters
             │
             ▼
[Schema Builder Module (schema_builder.py)]
  - Leverage Claude Opus 5.5 to synthesize routes into valid OpenAPI 3.0 paths
  - Deduce requestBody and parameter schemas from frontend mutation calls
             │
             ▼
[Exporter Module (exporter.py)]
  - Export openapi.json and openapi.yaml
  - Generate human-readable Markdown API Documentation
```

## Module Specifications

### 1. `config.py`
- Typed configuration class loading from `.env`.
- Parameters: `ANTHROPIC_API_KEY`, `ANTHROPIC_MODEL` (default `claude-3-opus-20240229`), `OUTPUT_DIR`, `MAX_SCRIPTS`, `REQUEST_TIMEOUT`.

### 2. `fetcher.py`
- Concurrent downloading of client-side assets from target web applications.
- Filters out common third-party analytics and tracking scripts.
- Beautifies minified code using `jsbeautifier`.

### 3. `extractor.py`
- Regex patterns for API route discovery (`/api/`, `/v1/`, `/v2/`, `/auth/`, `/users/`, `/admin/`, etc.).
- Identifies associated HTTP verbs (GET, POST, PUT, DELETE, PATCH).
- Extracts client-side payload models, mutation state, and query string patterns.

### 4. `schema_builder.py`
- Uses Anthropic API SDK to construct formal OpenAPI 3.0 path items and component schemas from code extracts.
- Handles rate-limiting and token batching.

### 5. `exporter.py`
- Writes validated `openapi.json`, `openapi.yaml`.
- Produces a summary Markdown document with route tables, methods, and parameters.

### 6. `main.py`
- CLI interface using `argparse` and `rich.console`.
- Commands: `--url <URL>`, `--file <JS_PATH>`, `--output <DIR>`, `--dry-run`.
