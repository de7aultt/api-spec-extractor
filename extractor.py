import re
from dataclasses import dataclass, field
from urllib.parse import parse_qsl, urlparse

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
    )
