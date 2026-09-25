import json
import re
from dataclasses import dataclass, field
from urllib.parse import parse_qsl, urlparse

from bs4 import BeautifulSoup

HTTP_METHODS = ("GET", "POST", "PUT", "DELETE", "PATCH")

ROUTE_KEYWORD = r"(?:api|v[1-9]\d*|graphql|rest|internal)"

ENDPOINT_LITERAL_PATTERN = re.compile(
    r"(?P<quote>['\"`])"
    r"(?P<value>(?:https?://[^\s'\"`/]+)?(?:/[\w.\-]+){0,2}?/" + ROUTE_KEYWORD +
    r"(?:[/?#][^'\"`\s]*)?)"
    r"(?P=quote)",
    re.IGNORECASE,
)

CONCATENATED_SUFFIX_PATTERN = re.compile(r"\s*\+\s*(?P<expression>[A-Za-z_$][\w$]*(?:\.[A-Za-z_$][\w$]*)*)")
TEMPLATE_EXPRESSION_PATTERN = re.compile(r"\$\{(?P<expression>[^}]*)\}")
COLON_PARAMETER_PATTERN = re.compile(r"(?<=/):(?P<name>[A-Za-z_][\w]*)")
IDENTIFIER_PATTERN = re.compile(r"[A-Za-z_$][\w$]*")

CLIENT_METHOD_CALL_PATTERN = re.compile(
    r"\.(?P<method>get|post|put|delete|patch)\s*(?:<[^<>()]*>)?\s*\(\s*$",
    re.IGNORECASE,
)
METHOD_OPTION_PATTERN = re.compile(
    r"\bmethod\s*:\s*['\"`](?P<method>get|post|put|delete|patch)['\"`]",
    re.IGNORECASE,
)
FETCH_CALL_PATTERN = re.compile(r"\bfetch\s*\(\s*$")
QUERY_HOOK_PATTERN = re.compile(r"\b(?:useQuery|useInfiniteQuery|useSWR|useSuspenseQuery)\b")
MUTATION_HOOK_PATTERN = re.compile(r"\buseMutation\b")

API_CLIENT_CALL_PATTERN = re.compile(
    r"\b(?:axios|fetch|useQuery|useMutation|useInfiniteQuery|useSuspenseQuery|useSWR|"
    r"XMLHttpRequest|\$http|apolloClient|gql)\b"
)

DECLARATION_PATTERN = re.compile(
    r"(?:\bfunction\b[^(]*\([^)]*\)\s*\{"
    r"|\b(?:const|let|var)\s+[\w$]+\s*=\s*(?:async\s*)?(?:\([^()]*\)|[\w$]+)\s*=>\s*\{"
    r"|\b(?:const|let|var)\s+[\w$]+\s*=\s*\{"
    r"|^\s*(?:async\s+)?[\w$]+\s*\([^()]*\)\s*\{"
    r"|\b[\w$]+\s*:\s*(?:async\s+)?(?:function\s*)?\([^()]*\)\s*(?:=>\s*)?\{)",
    re.MULTILINE,
)

STATE_ACCESS_PATTERN = re.compile(r"\b(?:state|rootState|getters)\.(?P<name>[A-Za-z_$][\w$]*)")
PAYLOAD_ACCESS_PATTERN = re.compile(r"\bpayload\.(?P<name>[A-Za-z_$][\w$]*)")
SLICE_NAME_PATTERN = re.compile(r"\bcreateSlice\s*\(\s*\{\s*name\s*:\s*['\"`](?P<name>[\w\-/]+)['\"`]")
STORE_NAME_PATTERN = re.compile(r"\bdefineStore\s*\(\s*['\"`](?P<name>[\w\-/]+)['\"`]")
COMMIT_PATTERN = re.compile(r"\b(?:commit|dispatch)\s*\(\s*['\"`](?P<name>[\w\-/:]+)['\"`]")
ACTION_TYPE_PATTERN = re.compile(r"\btype\s*:\s*['\"`](?P<name>[A-Z][A-Z0-9_]*(?:/[A-Za-z0-9_]+)*)['\"`]")
USE_STATE_PATTERN = re.compile(
    r"\b(?:const|let|var)\s*\[\s*(?P<name>[A-Za-z_$][\w$]*)\s*,\s*set[\w$]*\s*\]\s*=\s*(?:[\w$]+\.)?useState\b"
)
OBJECT_BLOCK_OPENERS = {
    "initialState": re.compile(r"\binitialState\s*[:=]\s*(?=\{)"),
    "state": re.compile(r"\bstate\s*:\s*(?:\(\s*\)\s*=>\s*\(\s*)?(?=\{)"),
    "mutation": re.compile(r"\b(?:mutations|reducers)\s*:\s*(?=\{)"),
    "action": re.compile(r"\bactions\s*:\s*(?=\{)"),
    "payload": re.compile(r"\b(?:JSON\.stringify\s*\(\s*|\bdata\s*:\s*|\bbody\s*:\s*|\bvariables\s*:\s*)(?=\{)"),
}
OBJECT_KEY_PATTERN = re.compile(
    r"^(?:async\s+)?(?:get\s+|set\s+)?(?:\*\s*)?['\"]?(?P<key>[A-Za-z_$][\w$\-]*)['\"]?\s*(?::|\(|$)"
)

DEFAULT_LOOKBACK_CHARS = 2500
DEFAULT_CONTEXT_LINES = 12
METHOD_LOOKBACK_CHARS = 200
METHOD_LOOKAHEAD_CHARS = 400
MAX_SAMPLES_PER_ENDPOINT = 3


@dataclass
class ScriptAnalysis:
    source: str
    endpoints: list[dict] = field(default_factory=list)
    state_models: list[str] = field(default_factory=list)
    code_blocks: list[str] = field(default_factory=list)
    ziggy_routes: list[dict] = field(default_factory=list)
    route_calls: list[dict] = field(default_factory=list)


def _parameter_name_from_expression(expression: str) -> str:
    identifiers = IDENTIFIER_PATTERN.findall(expression)
    if not identifiers:
        return "param"
    return identifiers[-1].lstrip("$") or "param"


def normalize_endpoint_path(raw_value: str) -> tuple[str, list[str]]:
    templated = TEMPLATE_EXPRESSION_PATTERN.sub(
        lambda match: "{" + _parameter_name_from_expression(match.group("expression")) + "}",
        raw_value,
    )
    parsed = urlparse(templated) if templated.lower().startswith(("http://", "https://")) else None
    path_part = parsed.path if parsed else templated
    query_part = parsed.query if parsed else ""
    if not parsed:
        path_part, _, remainder = path_part.partition("?")
        query_part = remainder.partition("#")[0]
        path_part = path_part.partition("#")[0]
    path_part = COLON_PARAMETER_PATTERN.sub(lambda match: "{" + match.group("name") + "}", path_part)
    path_part = re.sub(r"/{2,}", "/", path_part)
    if len(path_part) > 1:
        path_part = path_part.rstrip("/")
    query_names = [name for name, _ in parse_qsl(query_part, keep_blank_values=True) if name]
    return path_part or "/", query_names


def _detect_methods(content: str, start: int, end: int, path: str) -> tuple[set[str], bool]:
    lookback = content[max(0, start - METHOD_LOOKBACK_CHARS):start]
    lookahead = content[end:end + METHOD_LOOKAHEAD_CHARS]
    next_literal = ENDPOINT_LITERAL_PATTERN.search(lookahead)
    if next_literal:
        lookahead = lookahead[:next_literal.start()]
    methods: set[str] = set()
    client_call = CLIENT_METHOD_CALL_PATTERN.search(lookback)
    if client_call:
        methods.add(client_call.group("method").upper())
    methods.update(match.group("method").upper() for match in METHOD_OPTION_PATTERN.finditer(lookahead))
    if methods:
        return methods, False
    if FETCH_CALL_PATTERN.search(lookback):
        return {"GET"}, False
    wider_context = content[max(0, start - DEFAULT_LOOKBACK_CHARS // 5):start]
    if MUTATION_HOOK_PATTERN.search(wider_context):
        return {"POST"}, True
    if QUERY_HOOK_PATTERN.search(wider_context):
        return {"GET"}, True
    if "graphql" in path.lower():
        return {"POST"}, True
    return {"GET"}, True


def extract_endpoints(content: str) -> list[dict]:
    registry: dict[str, dict] = {}
    for match in ENDPOINT_LITERAL_PATTERN.finditer(content):
        raw_value = match.group("value")
        suffix_match = CONCATENATED_SUFFIX_PATTERN.match(content, match.end())
        if suffix_match and raw_value.endswith("/"):
            raw_value += "{" + _parameter_name_from_expression(suffix_match.group("expression")) + "}"
        path, query_names = normalize_endpoint_path(raw_value)
        methods, inferred = _detect_methods(content, match.start(), match.end(), path)
        line_number = content.count("\n", 0, match.start()) + 1
        entry = registry.get(path)
        if entry is None:
            entry = {
                "path": path,
                "methods": set(),
                "explicit_methods": set(),
                "query_params": [],
                "path_params": re.findall(r"\{([^{}]+)\}", path),
                "occurrences": 0,
                "lines": [],
                "samples": [],
            }
            registry[path] = entry
        entry["methods"].update(methods)
        if not inferred:
            entry["explicit_methods"].update(methods)
        entry["occurrences"] += 1
        entry["lines"].append(line_number)
        for name in query_names:
            if name not in entry["query_params"]:
                entry["query_params"].append(name)
        if len(entry["samples"]) < MAX_SAMPLES_PER_ENDPOINT and raw_value not in entry["samples"]:
            entry["samples"].append(raw_value)
    endpoints = []
    for entry in registry.values():
        explicit = entry.pop("explicit_methods")
        chosen = explicit or entry["methods"]
        entry["methods"] = [method for method in HTTP_METHODS if method in chosen]
        entry["method_inferred"] = not explicit
        endpoints.append(entry)
    return sorted(endpoints, key=lambda item: item["path"])


def merge_endpoints(endpoint_groups: list[list[dict]]) -> list[dict]:
    merged: dict[str, dict] = {}
    for group in endpoint_groups:
        for endpoint in group:
            existing = merged.get(endpoint["path"])
            if existing is None:
                merged[endpoint["path"]] = {
                    **endpoint,
                    "methods": list(endpoint["methods"]),
                    "query_params": list(endpoint["query_params"]),
                    "path_params": list(endpoint["path_params"]),
                    "lines": list(endpoint["lines"]),
                    "samples": list(endpoint["samples"]),
                    "route_names": list(endpoint.get("route_names", [])),
                    "sources": list(endpoint.get("sources", [])),
                }
                continue
            if existing["method_inferred"] and not endpoint["method_inferred"]:
                combined_methods = set(endpoint["methods"])
            elif endpoint["method_inferred"] and not existing["method_inferred"]:
                combined_methods = set(existing["methods"])
            else:
                combined_methods = set(existing["methods"]) | set(endpoint["methods"])
            existing["methods"] = [method for method in HTTP_METHODS if method in combined_methods]
            existing["method_inferred"] = existing["method_inferred"] and endpoint["method_inferred"]
            existing["occurrences"] += endpoint["occurrences"]
            existing["lines"].extend(endpoint["lines"])
            for name in endpoint["query_params"]:
                if name not in existing["query_params"]:
                    existing["query_params"].append(name)
            for sample in endpoint["samples"]:
                if len(existing["samples"]) < MAX_SAMPLES_PER_ENDPOINT and sample not in existing["samples"]:
                    existing["samples"].append(sample)
            for key in ("route_names", "sources"):
                for value in endpoint.get(key, []):
                    if value not in existing[key]:
                        existing[key].append(value)
    return sorted(merged.values(), key=lambda item: item["path"])


def _skip_string(content: str, index: int) -> int:
    quote = content[index]
    position = index + 1
    length = len(content)
    while position < length:
        character = content[position]
        if character == "\\":
            position += 2
            continue
        if character == quote:
            return position + 1
        position += 1
    return length


def _skip_comment(content: str, index: int) -> int:
    if content.startswith("//", index):
        newline = content.find("\n", index)
        return len(content) if newline == -1 else newline + 1
    closing = content.find("*/", index + 2)
    return len(content) if closing == -1 else closing + 2


def find_matching_brace(content: str, open_index: int, limit: int | None = None) -> int:
    if open_index >= len(content) or content[open_index] != "{":
        return -1
    depth = 0
    position = open_index
    boundary = len(content) if limit is None else min(len(content), open_index + limit)
    while position < boundary:
        character = content[position]
        if character in "'\"`":
            position = _skip_string(content, position)
            continue
        if content.startswith("//", position) or content.startswith("/*", position):
            position = _skip_comment(content, position)
            continue
        if character == "{":
            depth += 1
        elif character == "}":
            depth -= 1
            if depth == 0:
                return position
        position += 1
    return -1


def _split_top_level(segment: str) -> list[str]:
    parts: list[str] = []
    depth = 0
    current_start = 0
    position = 0
    length = len(segment)
    while position < length:
        character = segment[position]
        if character in "'\"`":
            position = _skip_string(segment, position)
            continue
        if character in "{[(":
            depth += 1
        elif character in "}])":
            depth -= 1
        elif character == "," and depth == 0:
            parts.append(segment[current_start:position])
            current_start = position + 1
        position += 1
    parts.append(segment[current_start:])
    return parts


def extract_object_keys(content: str, open_index: int) -> list[str]:
    close_index = find_matching_brace(content, open_index)
    if close_index == -1:
        return []
    keys: list[str] = []
    for part in _split_top_level(content[open_index + 1:close_index]):
        candidate = part.strip()
        if not candidate or candidate.startswith("..."):
            continue
        key_match = OBJECT_KEY_PATTERN.match(candidate)
        if key_match and key_match.group("key") not in keys:
            keys.append(key_match.group("key"))
    return keys


def extract_state_models(content: str) -> list[str]:
    models: set[str] = set()
    models.update(f"state.{match.group('name')}" for match in STATE_ACCESS_PATTERN.finditer(content))
    models.update(f"state.{match.group('name')}" for match in USE_STATE_PATTERN.finditer(content))
    models.update(f"payload.{match.group('name')}" for match in PAYLOAD_ACCESS_PATTERN.finditer(content))
    models.update(f"slice.{match.group('name')}" for match in SLICE_NAME_PATTERN.finditer(content))
    models.update(f"store.{match.group('name')}" for match in STORE_NAME_PATTERN.finditer(content))
    models.update(f"mutation.{match.group('name')}" for match in COMMIT_PATTERN.finditer(content))
    models.update(f"action.{match.group('name')}" for match in ACTION_TYPE_PATTERN.finditer(content))
    for prefix, pattern in OBJECT_BLOCK_OPENERS.items():
        for match in pattern.finditer(content):
            for key in extract_object_keys(content, match.end()):
                models.add(f"{prefix}.{key}")
    return sorted(models)


def _line_start(content: str, index: int) -> int:
    return content.rfind("\n", 0, index) + 1


def _line_end(content: str, index: int) -> int:
    newline = content.find("\n", index)
    return len(content) if newline == -1 else newline


def _context_window(content: str, index: int, lines: int) -> tuple[int, int]:
    start = _line_start(content, index)
    for _ in range(lines):
        if start == 0:
            break
        start = _line_start(content, start - 1)
    end = _line_end(content, index)
    for _ in range(lines):
        if end >= len(content):
            break
        end = _line_end(content, end + 1)
    return start, end


def _enclosing_block(content: str, index: int, max_chars: int) -> tuple[int, int]:
    search_start = max(0, index - DEFAULT_LOOKBACK_CHARS)
    declarations = list(DECLARATION_PATTERN.finditer(content, search_start, index))
    for declaration in reversed(declarations):
        open_index = content.rfind("{", declaration.start(), declaration.end())
        close_index = find_matching_brace(content, open_index, limit=max_chars * 4)
        if close_index != -1 and close_index >= index:
            return _line_start(content, declaration.start()), close_index + 1
    return _context_window(content, index, DEFAULT_CONTEXT_LINES)


def _clamp_range(content: str, start: int, end: int, anchor: int, max_chars: int) -> tuple[int, int]:
    if end - start <= max_chars:
        return start, end
    half_window = max_chars // 2
    clamped_start = max(start, anchor - half_window)
    clamped_end = min(end, clamped_start + max_chars)
    clamped_start = max(start, clamped_end - max_chars)
    if clamped_start > start:
        next_line_start = content.find("\n", clamped_start, anchor) + 1
        if next_line_start > 0:
            clamped_start = next_line_start
    if clamped_end < end:
        previous_line_end = content.rfind("\n", anchor, clamped_end)
        if previous_line_end > anchor:
            clamped_end = previous_line_end
    return clamped_start, clamped_end


def extract_candidate_code_blocks(content: str, max_chars_per_block: int = 4000) -> list[str]:
    if max_chars_per_block <= 0:
        raise ValueError("max_chars_per_block must be greater than zero")
    anchors = sorted(
        {match.start() for match in API_CLIENT_CALL_PATTERN.finditer(content)}
        | {match.start() for match in ENDPOINT_LITERAL_PATTERN.finditer(content)}
    )
    ranges: list[tuple[int, int]] = []
    for anchor in anchors:
        if ranges and ranges[-1][0] <= anchor < ranges[-1][1]:
            continue
        start, end = _enclosing_block(content, anchor, max_chars_per_block)
        ranges.append(_clamp_range(content, start, end, anchor, max_chars_per_block))
    merged: list[list[int]] = []
    for start, end in sorted(ranges):
        if merged and start <= merged[-1][1] and max(end, merged[-1][1]) - merged[-1][0] <= max_chars_per_block:
            merged[-1][1] = max(merged[-1][1], end)
        else:
            merged.append([start, end])
    blocks: list[str] = []
    seen: set[str] = set()
    for start, end in merged:
        block = content[start:end].strip()
        if block and block not in seen:
            seen.add(block)
            blocks.append(block)
    return blocks


def analyze_script(content: str, source: str, max_chars_per_block: int = 4000) -> ScriptAnalysis:
    return ScriptAnalysis(
        source=source,
        endpoints=extract_endpoints(content),
        state_models=extract_state_models(content),
        code_blocks=extract_candidate_code_blocks(content, max_chars_per_block),
        ziggy_routes=extract_ziggy_routes(content),
        route_calls=extract_route_calls(content),
    )


ZIGGY_ASSIGNMENT_PATTERN = re.compile(r"\bZiggy\s*=\s*(?=\{|JSON\.parse\s*\()")
ZIGGY_SHAPE_PATTERN = re.compile(r"\{\s*['\"]?url['\"]?\s*:\s*[^,{}]{1,300},\s*['\"]?port['\"]?\s*:")
JSON_PARSE_CALL_PATTERN = re.compile(r"JSON\.parse\s*\(\s*(?=['\"`])")
ROUTE_CALL_PATTERN = re.compile(r"(?<![\w$])(?:\$?route)\s*\(\s*(['\"`])(?P<name>[\w.\-:/]+)\1")
ZIGGY_PARAMETER_PATTERN = re.compile(r"\{(?P<name>[A-Za-z_][\w]*)\??\}")
NEXT_DYNAMIC_SEGMENT_PATTERN = re.compile(r"\[(?:\.\.\.)?(?P<name>[A-Za-z_][\w]*)\]")
JS_IDENTIFIER_START = re.compile(r"[A-Za-z_$]")
JS_IDENTIFIER_BODY = re.compile(r"[\w$]*")
JS_NUMBER_PATTERN = re.compile(r"-?(?:0[xX][0-9a-fA-F]+|(?:\d+\.?\d*|\.\d+)(?:[eE][+-]?\d+)?)")
JS_KEYWORD_VALUES = {"true": "true", "false": "false", "null": "null", "undefined": "null", "NaN": "null", "Infinity": "null"}
JS_SIMPLE_ESCAPES = {"n": "\n", "t": "\t", "r": "\r", "b": "\b", "f": "\f", "v": "\v", "0": "\0"}
MAX_STATE_MODEL_DEPTH = 3
MAX_ZIGGY_SCAN_CHARS = 2_000_000


class JavaScriptLiteralError(ValueError):
    pass


@dataclass
class FrameworkDetection:
    framework: str
    detail: str
    route_count: int = 0
    source: str = ""

    def as_dict(self) -> dict:
        return {
            "framework": self.framework,
            "detail": self.detail,
            "route_count": self.route_count,
            "source": self.source,
        }


@dataclass
class DocumentAnalysis:
    source: str
    ziggy_routes: list[dict] = field(default_factory=list)
    inertia_page: dict | None = None
    next_data: dict | None = None
    endpoints: list[dict] = field(default_factory=list)
    state_models: list[str] = field(default_factory=list)
    detections: list[FrameworkDetection] = field(default_factory=list)


def _decode_js_string(content: str, index: int) -> tuple[str, int]:
    quote = content[index]
    position = index + 1
    characters: list[str] = []
    length = len(content)
    while position < length:
        character = content[position]
        if character == quote:
            return "".join(characters), position + 1
        if quote == "`" and content.startswith("${", position):
            raise JavaScriptLiteralError("Template literal interpolation is not a static value")
        if character != "\\":
            characters.append(character)
            position += 1
            continue
        position += 1
        if position >= length:
            break
        escape = content[position]
        if escape in JS_SIMPLE_ESCAPES and not (escape == "0" and content[position + 1:position + 2].isdigit()):
            characters.append(JS_SIMPLE_ESCAPES[escape])
            position += 1
        elif escape == "x":
            characters.append(chr(int(content[position + 1:position + 3], 16)))
            position += 3
        elif escape == "u" and content.startswith("{", position + 1):
            closing = content.index("}", position)
            characters.append(chr(int(content[position + 2:closing], 16)))
            position = closing + 1
        elif escape == "u":
            characters.append(chr(int(content[position + 1:position + 5], 16)))
            position += 5
        elif escape in "\r\n":
            position += 2 if content.startswith("\r\n", position) else 1
        else:
            characters.append(escape)
            position += 1
    raise JavaScriptLiteralError("Unterminated string literal")


def _next_significant_character(content: str, index: int) -> str:
    position = index
    length = len(content)
    while position < length:
        if content[position].isspace():
            position += 1
            continue
        if content.startswith("//", position) or content.startswith("/*", position):
            position = _skip_comment(content, position)
            continue
        return content[position]
    return ""


def _drop_trailing_comma(parts: list[str]) -> None:
    while parts and parts[-1].isspace():
        parts.pop()
    if parts and parts[-1] == ",":
        parts.pop()


def javascript_literal_to_json(literal: str) -> str:
    parts: list[str] = []
    position = 0
    length = len(literal)
    while position < length:
        character = literal[position]
        if character.isspace():
            position += 1
            continue
        if literal.startswith("//", position) or literal.startswith("/*", position):
            position = _skip_comment(literal, position)
            continue
        if character in "'\"`":
            try:
                value, position = _decode_js_string(literal, position)
            except (ValueError, IndexError) as error:
                raise JavaScriptLiteralError(f"Invalid string literal: {error}") from error
            parts.append(json.dumps(value))
            continue
        if character in "}]":
            _drop_trailing_comma(parts)
            parts.append(character)
            position += 1
            continue
        if character in "{[:,":
            parts.append(character)
            position += 1
            continue
        if literal.startswith("!0", position) or literal.startswith("!1", position):
            parts.append("true" if literal[position + 1] == "0" else "false")
            position += 2
            continue
        if literal.startswith("void 0", position):
            parts.append("null")
            position += 6
            continue
        number_match = JS_NUMBER_PATTERN.match(literal, position)
        if number_match and (character.isdigit() or character in "-."):
            token = number_match.group()
            is_key = _next_significant_character(literal, number_match.end()) == ":"
            if is_key:
                parts.append(json.dumps(token))
            elif token.lower().lstrip("-").startswith("0x"):
                parts.append(str(int(token, 16)))
            else:
                normalized = token.replace("-.", "-0.")
                parts.append("0" + normalized if normalized.startswith(".") else normalized)
            position = number_match.end()
            continue
        if JS_IDENTIFIER_START.match(character):
            identifier_end = JS_IDENTIFIER_BODY.match(literal, position + 1).end()
            identifier = literal[position:identifier_end]
            if _next_significant_character(literal, identifier_end) == ":":
                parts.append(json.dumps(identifier))
            elif identifier in JS_KEYWORD_VALUES:
                parts.append(JS_KEYWORD_VALUES[identifier])
            else:
                raise JavaScriptLiteralError(f"Unsupported identifier '{identifier}' in literal")
            position = identifier_end
            continue
        raise JavaScriptLiteralError(f"Unexpected character '{character}' at position {position}")
    return "".join(parts)


def parse_javascript_literal(literal: str) -> object:
    try:
        return json.loads(literal)
    except ValueError:
        pass
    try:
        return json.loads(javascript_literal_to_json(literal))
    except ValueError as error:
        raise JavaScriptLiteralError(f"Unable to parse JavaScript literal: {error}") from error


def _read_object_literal(content: str, index: int) -> object | None:
    if content.startswith("{", index):
        close_index = find_matching_brace(content, index)
        if close_index == -1:
            return None
        literal = content[index:close_index + 1]
    else:
        call_match = JSON_PARSE_CALL_PATTERN.match(content, index)
        if not call_match:
            return None
        try:
            literal, _ = _decode_js_string(content, call_match.end())
        except (ValueError, IndexError):
            return None
    try:
        return parse_javascript_literal(literal)
    except JavaScriptLiteralError:
        return None


def _is_ziggy_config(candidate: object) -> bool:
    if not isinstance(candidate, dict):
        return False
    routes = candidate.get("routes")
    return isinstance(routes, dict) and bool(routes) and all(
        isinstance(route, dict) and isinstance(route.get("uri"), str) for route in routes.values()
    )


def normalize_ziggy_uri(uri: str, base_url: str | None = None) -> str:
    prefix = ""
    if base_url:
        prefix = urlparse(base_url).path.rstrip("/")
    cleaned = ZIGGY_PARAMETER_PATTERN.sub(lambda match: "{" + match.group("name") + "}", uri.strip())
    cleaned = "/" + cleaned.strip("/")
    combined = f"{prefix}{cleaned}" if cleaned != "/" else (prefix or "/")
    return re.sub(r"/{2,}", "/", combined)


def ziggy_config_to_routes(config: dict) -> list[dict]:
    if not _is_ziggy_config(config):
        return []
    base_url = config.get("url") if isinstance(config.get("url"), str) else None
    routes: list[dict] = []
    for name, definition in config["routes"].items():
        raw_methods = definition.get("methods") or ["GET"]
        methods = [method for method in HTTP_METHODS if method in {str(item).upper() for item in raw_methods}]
        uri = definition["uri"]
        routes.append({
            "name": str(name),
            "uri": uri,
            "path": normalize_ziggy_uri(uri, base_url),
            "methods": methods or ["GET"],
            "parameters": ZIGGY_PARAMETER_PATTERN.findall(uri),
            "optional_parameters": re.findall(r"\{([A-Za-z_][\w]*)\?\}", uri),
            "domain": definition.get("domain"),
            "wheres": definition.get("wheres") if isinstance(definition.get("wheres"), dict) else {},
        })
    return routes


def _merge_ziggy_routes(route_groups: list[list[dict]]) -> list[dict]:
    merged: dict[str, dict] = {}
    for group in route_groups:
        for route in group:
            merged.setdefault(route["name"], route)
    return sorted(merged.values(), key=lambda route: (route["path"], route["name"]))


def extract_ziggy_routes(content: str) -> list[dict]:
    scan_region = content[:MAX_ZIGGY_SCAN_CHARS]
    candidate_indices = sorted(
        {match.end() for match in ZIGGY_ASSIGNMENT_PATTERN.finditer(scan_region)}
        | {match.start() for match in ZIGGY_SHAPE_PATTERN.finditer(scan_region)}
    )
    route_groups: list[list[dict]] = []
    consumed_until = -1
    for index in candidate_indices:
        if index < consumed_until:
            continue
        config = _read_object_literal(content, index)
        if not _is_ziggy_config(config):
            continue
        route_groups.append(ziggy_config_to_routes(config))
        if content.startswith("{", index):
            consumed_until = find_matching_brace(content, index)
    return _merge_ziggy_routes(route_groups)


def extract_route_calls(content: str) -> list[dict]:
    calls: list[dict] = []
    for match in ROUTE_CALL_PATTERN.finditer(content):
        lookback = content[max(0, match.start() - METHOD_LOOKBACK_CHARS):match.start()]
        client_call = CLIENT_METHOD_CALL_PATTERN.search(lookback)
        calls.append({
            "name": match.group("name"),
            "method": client_call.group("method").upper() if client_call else None,
            "line": content.count("\n", 0, match.start()) + 1,
        })
    return calls


def ziggy_routes_to_endpoints(routes: list[dict], source: str = "ziggy") -> list[dict]:
    endpoints: list[dict] = []
    for route in routes:
        endpoints.append({
            "path": route["path"],
            "methods": list(route["methods"]),
            "query_params": [],
            "path_params": re.findall(r"\{([^{}]+)\}", route["path"]),
            "occurrences": 1,
            "lines": [],
            "samples": [route["uri"]],
            "method_inferred": False,
            "route_names": [route["name"]],
            "sources": [source],
        })
    return merge_endpoints([endpoints])


def resolve_route_calls(calls: list[dict], routes: list[dict]) -> tuple[list[dict], list[str]]:
    routes_by_name = {route["name"]: route for route in routes}
    endpoints: list[dict] = []
    unresolved: list[str] = []
    for call in calls:
        route = routes_by_name.get(call["name"])
        if route is None:
            if call["name"] not in unresolved:
                unresolved.append(call["name"])
            continue
        methods = [call["method"]] if call["method"] else list(route["methods"])
        endpoints.append({
            "path": route["path"],
            "methods": methods,
            "query_params": [],
            "path_params": re.findall(r"\{([^{}]+)\}", route["path"]),
            "occurrences": 1,
            "lines": [call["line"]],
            "samples": [f"route('{call['name']}')"],
            "method_inferred": False,
            "route_names": [route["name"]],
            "sources": ["route-call"],
        })
    return merge_endpoints([endpoints]), unresolved


def _collect_state_paths(value: object, prefix: str, depth: int, collected: set[str]) -> None:
    if not isinstance(value, dict) or depth > MAX_STATE_MODEL_DEPTH:
        return
    for key, nested in value.items():
        path = f"{prefix}.{key}"
        collected.add(path)
        _collect_state_paths(nested, path, depth + 1, collected)


def _endpoints_from_data(value: object, source: str) -> list[dict]:
    serialized = json.dumps(value, ensure_ascii=False)
    endpoints = extract_endpoints(serialized)
    for endpoint in endpoints:
        endpoint["lines"] = []
        endpoint["route_names"] = []
        endpoint["sources"] = [source]
    return endpoints


def extract_inertia_page(html: str) -> dict | None:
    soup = BeautifulSoup(html, "html.parser")
    for element in soup.find_all(attrs={"data-page": True}):
        attribute_value = (element.get("data-page") or "").strip()
        raw_payload = attribute_value if attribute_value.startswith("{") else (element.string or element.get_text() or "").strip()
        if not raw_payload.startswith("{"):
            continue
        try:
            payload = json.loads(raw_payload)
        except ValueError:
            continue
        if isinstance(payload, dict) and ("component" in payload or "props" in payload):
            return payload
    return None


def extract_next_data(html: str) -> dict | None:
    soup = BeautifulSoup(html, "html.parser")
    script_tag = soup.find("script", id="__NEXT_DATA__")
    if script_tag is None:
        return None
    try:
        payload = json.loads(script_tag.string or script_tag.get_text() or "")
    except ValueError:
        return None
    return payload if isinstance(payload, dict) else None


def next_data_route(next_data: dict) -> str | None:
    build_id = next_data.get("buildId")
    page = next_data.get("page")
    if not isinstance(build_id, str) or not isinstance(page, str):
        return None
    normalized_page = NEXT_DYNAMIC_SEGMENT_PATTERN.sub(lambda match: "{" + match.group("name") + "}", page)
    page_segment = "/index" if normalized_page in {"", "/"} else normalized_page
    return f"/_next/data/{build_id}{page_segment}.json"


def analyze_html_document(html: str, source: str) -> DocumentAnalysis:
    analysis = DocumentAnalysis(source=source)
    endpoint_groups: list[list[dict]] = []
    state_models: set[str] = set()
    html_routes = extract_ziggy_routes(html)
    if html_routes:
        analysis.detections.append(FrameworkDetection(
            framework="Laravel Ziggy",
            detail=f"{len(html_routes)} routes in HTML",
            route_count=len(html_routes),
            source=source,
        ))
    route_groups = [html_routes]
    inertia_page = extract_inertia_page(html)
    if inertia_page is not None:
        analysis.inertia_page = inertia_page
        props = inertia_page.get("props") if isinstance(inertia_page.get("props"), dict) else {}
        props_without_ziggy = {key: value for key, value in props.items() if key != "ziggy"}
        _collect_state_paths(props_without_ziggy, "inertia.props", 1, state_models)
        shared_ziggy = props.get("ziggy")
        props_routes = ziggy_config_to_routes(shared_ziggy) if isinstance(shared_ziggy, dict) else []
        if props_routes:
            route_groups.append(props_routes)
            analysis.detections.append(FrameworkDetection(
                framework="Laravel Ziggy",
                detail=f"{len(props_routes)} routes in Inertia props",
                route_count=len(props_routes),
                source=source,
            ))
        endpoint_groups.append(_endpoints_from_data(props_without_ziggy, "inertia-props"))
        component = inertia_page.get("component") or "unknown component"
        analysis.detections.append(FrameworkDetection(
            framework="Inertia.js",
            detail=f"page component '{component}' with {len(props)} props",
            source=source,
        ))
    next_data = extract_next_data(html)
    if next_data is not None:
        analysis.next_data = next_data
        page_props = next_data.get("props", {}).get("pageProps") if isinstance(next_data.get("props"), dict) else None
        _collect_state_paths(page_props, "next.pageProps", 1, state_models)
        endpoint_groups.append(_endpoints_from_data(next_data.get("props", {}), "next-data"))
        data_route = next_data_route(next_data)
        next_endpoints: list[dict] = []
        if data_route:
            next_endpoints.append({
                "path": data_route,
                "methods": ["GET"],
                "query_params": [],
                "path_params": re.findall(r"\{([^{}]+)\}", data_route),
                "occurrences": 1,
                "lines": [],
                "samples": [data_route],
                "method_inferred": False,
                "route_names": [str(next_data.get("page"))],
                "sources": ["next-data"],
            })
            endpoint_groups.append(next_endpoints)
        analysis.detections.append(FrameworkDetection(
            framework="Next.js",
            detail=f"page '{next_data.get('page', 'unknown')}' (build {next_data.get('buildId', 'unknown')})",
            route_count=len(next_endpoints),
            source=source,
        ))
    analysis.ziggy_routes = _merge_ziggy_routes(route_groups)
    endpoint_groups.append(ziggy_routes_to_endpoints(analysis.ziggy_routes))
    analysis.endpoints = merge_endpoints(endpoint_groups)
    analysis.state_models = sorted(state_models)
    return analysis


def combine_ziggy_routes(route_groups: list[list[dict]]) -> list[dict]:
    return _merge_ziggy_routes(route_groups)
