import json
import random
import re
import time
from dataclasses import dataclass, field

import anthropic

from config import Settings

SYSTEM_PROMPT = """You are an automated API architecture assistant and OpenAPI 3.0 synthesizer.
Analyze the provided client-side JavaScript endpoint declarations, request structures, and data models.
Synthesize a compliant OpenAPI 3.0 path object catalog including:
1. Full endpoint paths and normalized HTTP methods.
2. Query parameters, path parameters, and header expectations.
3. Reconstructed request body schemas and field types (string, integer, boolean, object).
4. Probable response status codes and schema structures.
Return your response STRICTLY as valid JSON matching OpenAPI 3.0 path structures."""

OUTPUT_CONTRACT = """Output contract:
- Respond with a single JSON object and nothing else: no Markdown fences, no prose.
- The object has exactly two top-level keys: "paths" and "components".
- "paths" maps each path (starting with "/", using {name} placeholders for path parameters) to an OpenAPI 3.0 Path Item Object.
- Operation keys are lowercase HTTP methods: get, post, put, delete, patch.
- Every operation includes "summary", "operationId", "tags", "parameters" (possibly empty), and "responses" with at least one status code.
- Every path parameter placeholder is declared with "in": "path" and "required": true.
- Request bodies use "requestBody" with "content" -> "application/json" -> "schema".
- "components" contains "schemas" with reusable object schemas referenced via "$ref": "#/components/schemas/Name".
- Use only OpenAPI 3.0 schema keywords (type, format, properties, items, required, enum, nullable, description, $ref, oneOf, anyOf, allOf).
- Base every endpoint and field on evidence in the provided material; mark uncertain details in "description"."""

MAX_RATE_LIMIT_RETRIES = 4
BASE_RETRY_DELAY_SECONDS = 5.0
MAX_RETRY_DELAY_SECONDS = 90.0
JSON_FENCE_PATTERN = re.compile(r"```(?:json)?\s*(?P<body>.*?)```", re.DOTALL | re.IGNORECASE)
PATH_PARAMETER_PATTERN = re.compile(r"\{([^{}]+)\}")
OPERATION_KEYS = ("get", "put", "post", "delete", "options", "head", "patch", "trace")


class SchemaSynthesisError(RuntimeError):
    pass


@dataclass
class SynthesisResult:
    paths: dict = field(default_factory=dict)
    components: dict = field(default_factory=lambda: {"schemas": {}})
    batches_total: int = 0
    batches_succeeded: int = 0
    warnings: list[str] = field(default_factory=list)
    input_tokens: int = 0
    output_tokens: int = 0


def _format_endpoint_digest(endpoints: list[dict]) -> str:
    digest = [
        {
            "path": endpoint["path"],
            "methods": endpoint["methods"],
            "method_inferred": endpoint["method_inferred"],
            "query_params": endpoint["query_params"],
            "path_params": endpoint["path_params"],
            "samples": endpoint["samples"],
        }
        for endpoint in endpoints
    ]
    return json.dumps(digest, indent=2)


def build_batches(code_blocks: list[str], char_budget: int) -> list[list[str]]:
    batches: list[list[str]] = []
    current: list[str] = []
    current_size = 0
    for block in code_blocks:
        block_size = len(block)
        if current and current_size + block_size > char_budget:
            batches.append(current)
            current = []
            current_size = 0
        current.append(block)
        current_size += block_size
    if current:
        batches.append(current)
    return batches or [[]]


def build_user_message(
    endpoints: list[dict],
    state_models: list[str],
    code_blocks: list[str],
    batch_index: int,
    batch_count: int,
) -> str:
    sections = [
        f"Batch {batch_index} of {batch_count}.",
        "Statically discovered endpoints (JSON):",
        _format_endpoint_digest(endpoints),
        "Client-side state models and payload properties:",
        "\n".join(state_models) if state_models else "(none detected)",
        "Relevant client-side code excerpts:",
    ]
    if code_blocks:
        sections.extend(
            f"<excerpt index=\"{position}\">\n{block}\n</excerpt>"
            for position, block in enumerate(code_blocks, start=1)
        )
    else:
        sections.append("(no excerpts in this batch)")
    sections.append(OUTPUT_CONTRACT)
    return "\n\n".join(sections)


def _extract_json_text(raw_text: str) -> str:
    fenced = JSON_FENCE_PATTERN.search(raw_text)
    candidate = fenced.group("body") if fenced else raw_text
    start = candidate.find("{")
    end = candidate.rfind("}")
    if start == -1 or end == -1 or end < start:
        raise SchemaSynthesisError("Model response does not contain a JSON object")
    return candidate[start:end + 1]


def parse_model_response(raw_text: str) -> tuple[dict, dict]:
    try:
        payload = json.loads(_extract_json_text(raw_text))
    except json.JSONDecodeError as error:
        raise SchemaSynthesisError(f"Model response is not valid JSON: {error.msg} at position {error.pos}") from error
    if not isinstance(payload, dict):
        raise SchemaSynthesisError("Model response JSON must be an object")
    if "paths" in payload:
        paths = payload.get("paths") or {}
        components = payload.get("components") or {}
    else:
        paths = {key: value for key, value in payload.items() if key.startswith("/")}
        components = {}
    if not isinstance(paths, dict) or not isinstance(components, dict):
        raise SchemaSynthesisError("Model response has malformed 'paths' or 'components' sections")
    return paths, components


def merge_path_items(target: dict, incoming: dict) -> None:
    for path, path_item in incoming.items():
        if not isinstance(path, str) or not isinstance(path_item, dict):
            continue
        normalized_path = path if path.startswith("/") else f"/{path}"
        existing = target.setdefault(normalized_path, {})
        for key, value in path_item.items():
            normalized_key = key.lower() if key.lower() in OPERATION_KEYS else key
            if normalized_key not in existing:
                existing[normalized_key] = value


def merge_components(target: dict, incoming: dict) -> None:
    for section, entries in incoming.items():
        if not isinstance(entries, dict):
            continue
        destination = target.setdefault(section, {})
        for name, definition in entries.items():
            destination.setdefault(name, definition)


def _operation_id(method: str, path: str) -> str:
    tokens = re.findall(r"[A-Za-z0-9]+", PATH_PARAMETER_PATTERN.sub(lambda match: f"by {match.group(1)}", path))
    suffix = "".join(token[:1].upper() + token[1:] for token in tokens) or "Root"
    return f"{method}{suffix}"


def _tag_for_path(path: str) -> str:
    segments = [segment for segment in path.split("/") if segment and not segment.startswith("{")]
    meaningful = [segment for segment in segments if not re.fullmatch(r"(?:api|v\d+|rest|internal)", segment, re.IGNORECASE)]
    return (meaningful or segments or ["default"])[0]


def build_heuristic_paths(endpoints: list[dict]) -> dict:
    paths: dict = {}
    for endpoint in endpoints:
        path_item: dict = {}
        path_parameters = [
            {"name": name, "in": "path", "required": True, "schema": {"type": "string"}}
            for name in PATH_PARAMETER_PATTERN.findall(endpoint["path"])
        ]
        query_parameters = [
            {"name": name, "in": "query", "required": False, "schema": {"type": "string"}}
            for name in endpoint["query_params"]
        ]
        for method in endpoint["methods"] or ["GET"]:
            operation = {
                "summary": f"{method} {endpoint['path']}",
                "operationId": _operation_id(method.lower(), endpoint["path"]),
                "tags": [_tag_for_path(endpoint["path"])],
                "parameters": path_parameters + (query_parameters if method == "GET" else []),
                "responses": {"200": {"description": "Successful response"}},
            }
            if endpoint["method_inferred"]:
                operation["description"] = "HTTP method inferred heuristically from surrounding code."
            if method in {"POST", "PUT", "PATCH"}:
                operation["requestBody"] = {
                    "required": True,
                    "content": {"application/json": {"schema": {"type": "object"}}},
                }
            path_item[method.lower()] = operation
        paths[endpoint["path"]] = path_item
    return paths


class SchemaBuilder:
    def __init__(self, settings: Settings, client: anthropic.Anthropic | None = None):
        self.settings = settings
        self.client = client or anthropic.Anthropic(
            api_key=settings.anthropic_api_key,
            timeout=anthropic.Timeout(900.0, connect=settings.request_timeout),
        )

    def _request_parameters(self, user_message: str) -> dict:
        parameters = {
            "model": self.settings.anthropic_model,
            "max_tokens": self.settings.max_output_tokens,
            "system": SYSTEM_PROMPT,
            "messages": [{"role": "user", "content": user_message}],
        }
        if self.settings.anthropic_effort:
            parameters["output_config"] = {"effort": self.settings.anthropic_effort}
        return parameters

    def _retry_delay(self, error: anthropic.APIStatusError, attempt: int) -> float:
        retry_after = error.response.headers.get("retry-after") if error.response is not None else None
        try:
            if retry_after is not None:
                return min(float(retry_after), MAX_RETRY_DELAY_SECONDS)
        except ValueError:
            pass
        return min(BASE_RETRY_DELAY_SECONDS * (2 ** attempt) + random.uniform(0, 1), MAX_RETRY_DELAY_SECONDS)

    def _call_model(self, user_message: str, notify) -> anthropic.types.Message:
        parameters = self._request_parameters(user_message)
        for attempt in range(MAX_RATE_LIMIT_RETRIES + 1):
            try:
                with self.client.messages.stream(**parameters) as stream:
                    return stream.get_final_message()
            except (anthropic.RateLimitError, anthropic.InternalServerError) as error:
                if attempt == MAX_RATE_LIMIT_RETRIES:
                    raise SchemaSynthesisError(
                        f"Anthropic API is still unavailable after {MAX_RATE_LIMIT_RETRIES} retries (HTTP {error.status_code})"
                    ) from error
                delay = self._retry_delay(error, attempt)
                notify(f"Anthropic API returned HTTP {error.status_code}; retrying in {delay:.1f}s")
                time.sleep(delay)
            except anthropic.AuthenticationError as error:
                raise SchemaSynthesisError("Anthropic API rejected the credentials; check ANTHROPIC_API_KEY") from error
            except anthropic.PermissionDeniedError as error:
                raise SchemaSynthesisError("Anthropic API key lacks permission for this model or operation") from error
            except anthropic.NotFoundError as error:
                raise SchemaSynthesisError(
                    f"Model '{self.settings.anthropic_model}' was not found; check ANTHROPIC_MODEL"
                ) from error
            except anthropic.BadRequestError as error:
                raise SchemaSynthesisError(f"Anthropic API rejected the request: {error.message}") from error
            except anthropic.APIStatusError as error:
                raise SchemaSynthesisError(f"Anthropic API error (HTTP {error.status_code}): {error.message}") from error
            except anthropic.APITimeoutError as error:
                raise SchemaSynthesisError("Anthropic API request timed out") from error
            except anthropic.APIConnectionError as error:
                raise SchemaSynthesisError(f"Unable to reach the Anthropic API: {error}") from error
        raise SchemaSynthesisError("Anthropic API call did not complete")

    def synthesize(
        self,
        endpoints: list[dict],
        state_models: list[str],
        code_blocks: list[str],
        notify=None,
    ) -> SynthesisResult:
        report = notify or (lambda message: None)
        if not self.settings.has_api_key:
            raise SchemaSynthesisError("ANTHROPIC_API_KEY is not configured; use --dry-run to skip synthesis")
        result = SynthesisResult()
        batches = build_batches(code_blocks, self.settings.batch_char_budget)
        result.batches_total = len(batches)
        for batch_index, batch in enumerate(batches, start=1):
            report(f"Synthesizing batch {batch_index}/{len(batches)} ({len(batch)} code excerpts)")
            message_text = build_user_message(endpoints, state_models, batch, batch_index, len(batches))
            try:
                response = self._call_model(message_text, report)
            except SchemaSynthesisError as error:
                if isinstance(error.__cause__, (anthropic.AuthenticationError, anthropic.NotFoundError, anthropic.PermissionDeniedError)):
                    raise
                result.warnings.append(f"Batch {batch_index}: {error}")
                continue
            result.input_tokens += response.usage.input_tokens
            result.output_tokens += response.usage.output_tokens
            if response.stop_reason == "refusal":
                result.warnings.append(f"Batch {batch_index}: the model declined to process this batch")
                continue
            if response.stop_reason == "max_tokens":
                result.warnings.append(f"Batch {batch_index}: response hit the max_tokens limit and may be truncated")
            response_text = "".join(block.text for block in response.content if block.type == "text")
            try:
                paths, components = parse_model_response(response_text)
            except SchemaSynthesisError as error:
                result.warnings.append(f"Batch {batch_index}: {error}")
                continue
            merge_path_items(result.paths, paths)
            merge_components(result.components, components)
            result.batches_succeeded += 1
        if result.batches_succeeded == 0:
            raise SchemaSynthesisError(
                "No batch produced a usable OpenAPI response: " + "; ".join(result.warnings or ["unknown error"])
            )
        return result
