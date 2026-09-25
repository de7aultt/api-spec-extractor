import json
import re
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlparse

import yaml

OPENAPI_VERSION = "3.0.3"
HTTP_OPERATION_KEYS = ("get", "put", "post", "delete", "options", "head", "patch", "trace")
PATH_PARAMETER_PATTERN = re.compile(r"\{([^{}]+)\}")
COMPONENT_NAME_PATTERN = re.compile(r"[^A-Za-z0-9._-]")


class ExportError(RuntimeError):
    pass


@dataclass(frozen=True)
class ExportedFiles:
    json_path: Path
    yaml_path: Path
    catalog_path: Path


class _IndentedDumper(yaml.SafeDumper):
    def increase_indent(self, flow: bool = False, indentless: bool = False):
        return super().increase_indent(flow, False)


def _ensure_path_parameters(path: str, path_item: dict) -> None:
    placeholders = PATH_PARAMETER_PATTERN.findall(path)
    shared_parameters = path_item.get("parameters", []) if isinstance(path_item.get("parameters"), list) else []
    shared_names = {
        parameter.get("name") for parameter in shared_parameters
        if isinstance(parameter, dict) and parameter.get("in") == "path"
    }
    for key in HTTP_OPERATION_KEYS:
        operation = path_item.get(key)
        if not isinstance(operation, dict):
            continue
        parameters = operation.get("parameters")
        if not isinstance(parameters, list):
            parameters = []
        parameters = [parameter for parameter in parameters if isinstance(parameter, dict)]
        for parameter in parameters:
            if parameter.get("in") == "path":
                parameter["required"] = True
            if "schema" not in parameter and "content" not in parameter:
                parameter["schema"] = {"type": "string"}
        declared = shared_names | {parameter.get("name") for parameter in parameters if parameter.get("in") == "path"}
        for name in placeholders:
            if name not in declared:
                parameters.append({"name": name, "in": "path", "required": True, "schema": {"type": "string"}})
        operation["parameters"] = parameters


def _normalize_operation(operation: dict) -> dict:
    responses = operation.get("responses")
    if not isinstance(responses, dict) or not responses:
        responses = {"200": {"description": "Successful response"}}
    normalized_responses = {}
    for status, response in responses.items():
        response_object = response if isinstance(response, dict) else {}
        response_object.setdefault("description", "Response")
        normalized_responses[str(status)] = response_object
    operation["responses"] = normalized_responses
    if "tags" in operation and not isinstance(operation["tags"], list):
        operation["tags"] = [str(operation["tags"])]
    return operation


def normalize_paths(paths: dict) -> dict:
    normalized: dict = {}
    for raw_path in sorted(paths):
        path_item = paths[raw_path]
        if not isinstance(path_item, dict):
            continue
        path = raw_path if raw_path.startswith("/") else f"/{raw_path}"
        cleaned_item = {}
        for key, value in path_item.items():
            lowered = key.lower()
            if lowered in HTTP_OPERATION_KEYS:
                if isinstance(value, dict):
                    cleaned_item[lowered] = _normalize_operation(value)
            elif key == "parameters":
                if isinstance(value, list):
                    cleaned_item[key] = [parameter for parameter in value if isinstance(parameter, dict)]
            elif key in {"summary", "description", "servers"} or key.startswith("x-"):
                cleaned_item[key] = value
        if not any(key in HTTP_OPERATION_KEYS for key in cleaned_item):
            continue
        _ensure_path_parameters(path, cleaned_item)
        normalized[path] = cleaned_item
    return normalized


def _normalize_components(components: dict | None) -> dict:
    if not isinstance(components, dict):
        return {}
    normalized = {}
    for section, entries in components.items():
        if not isinstance(entries, dict) or not entries:
            continue
        normalized[section] = {
            COMPONENT_NAME_PATTERN.sub("_", str(name)): definition for name, definition in sorted(entries.items())
        }
    return normalized


def _server_url(target: str | None) -> str | None:
    if not target:
        return None
    parsed = urlparse(target)
    if parsed.scheme in {"http", "https"} and parsed.netloc:
        return f"{parsed.scheme}://{parsed.netloc}"
    return None


def build_openapi_document(
    paths: dict,
    components: dict | None = None,
    title: str = "Reconstructed API",
    version: str = "1.0.0",
    target: str | None = None,
    description: str | None = None,
) -> dict:
    normalized_paths = normalize_paths(paths)
    document: dict = {
        "openapi": OPENAPI_VERSION,
        "info": {
            "title": title,
            "version": version,
            "description": description or "OpenAPI 3.0 specification reconstructed from client-side JavaScript assets.",
        },
    }
    server_url = _server_url(target)
    if server_url:
        document["servers"] = [{"url": server_url}]
    document["paths"] = normalized_paths
    tag_names = sorted({
        tag
        for path_item in normalized_paths.values()
        for key, operation in path_item.items()
        if key in HTTP_OPERATION_KEYS
        for tag in operation.get("tags", [])
        if isinstance(tag, str)
    })
    if tag_names:
        document["tags"] = [{"name": name} for name in tag_names]
    normalized_components = _normalize_components(components)
    if normalized_components:
        document["components"] = normalized_components
    validate_openapi_document(document)
    return document


def validate_openapi_document(document: dict) -> None:
    if not str(document.get("openapi", "")).startswith("3.0"):
        raise ExportError("Document must declare an OpenAPI 3.0.x version")
    info = document.get("info")
    if not isinstance(info, dict) or not info.get("title") or not info.get("version"):
        raise ExportError("Document info must include a title and a version")
    paths = document.get("paths")
    if not isinstance(paths, dict):
        raise ExportError("Document paths must be an object")
    for path, path_item in paths.items():
        if not path.startswith("/"):
            raise ExportError(f"Path '{path}' must start with '/'")
        for key in HTTP_OPERATION_KEYS:
            operation = path_item.get(key)
            if operation is None:
                continue
            if not isinstance(operation.get("responses"), dict) or not operation["responses"]:
                raise ExportError(f"Operation {key.upper()} {path} must define at least one response")
            path_parameters = {
                parameter.get("name") for parameter in operation.get("parameters", []) + path_item.get("parameters", [])
                if isinstance(parameter, dict) and parameter.get("in") == "path"
            }
            missing = set(PATH_PARAMETER_PATTERN.findall(path)) - path_parameters
            if missing:
                raise ExportError(f"Operation {key.upper()} {path} is missing path parameters: {', '.join(sorted(missing))}")


def _required_parameters(path_item: dict, operation: dict) -> list[str]:
    parameters = [
        parameter for parameter in path_item.get("parameters", []) + operation.get("parameters", [])
        if isinstance(parameter, dict)
    ]
    labels = [
        f"{parameter.get('name')} ({parameter.get('in')})"
        for parameter in parameters
        if parameter.get("required")
    ]
    request_body = operation.get("requestBody")
    if isinstance(request_body, dict) and request_body.get("required"):
        labels.append("body")
    return labels


def _escape_markdown_cell(value: str) -> str:
    return value.replace("|", "\\|").replace("\n", " ").strip()


def _schema_type_label(schema: dict) -> str:
    if not isinstance(schema, dict):
        return "unknown"
    if "$ref" in schema:
        return schema["$ref"].rsplit("/", 1)[-1]
    schema_type = schema.get("type", "object")
    if schema_type == "array":
        return f"array<{_schema_type_label(schema.get('items', {}))}>"
    return str(schema_type)


def render_catalog(document: dict, state_models: list[str] | None = None, source_label: str | None = None) -> str:
    lines = [f"# {document['info']['title']}", ""]
    if source_label:
        lines.extend([f"Source: `{source_label}`", ""])
    lines.extend([f"OpenAPI version: `{document['openapi']}`", ""])
    operations = [
        (path, key.upper(), path_item, operation)
        for path, path_item in document["paths"].items()
        for key, operation in path_item.items()
        if key in HTTP_OPERATION_KEYS
    ]
    lines.extend(["## Routes", "", f"Total operations: {len(operations)}", ""])
    if operations:
        lines.extend(["| Method | Path | Summary | Required Parameters |", "| --- | --- | --- | --- |"])
        for path, method, path_item, operation in operations:
            required = ", ".join(_required_parameters(path_item, operation)) or "-"
            summary = operation.get("summary") or operation.get("operationId") or "-"
            lines.append(
                f"| {method} | `{_escape_markdown_cell(path)}` | {_escape_markdown_cell(str(summary))} | {_escape_markdown_cell(required)} |"
            )
    else:
        lines.append("No routes were discovered.")
    lines.append("")
    schemas = document.get("components", {}).get("schemas", {})
    lines.extend(["## Data Models", ""])
    if schemas:
        for name, schema in schemas.items():
            lines.extend([f"### {name}", ""])
            properties = schema.get("properties", {}) if isinstance(schema, dict) else {}
            if not properties:
                lines.extend([f"Type: `{_schema_type_label(schema)}`", ""])
                continue
            required_fields = set(schema.get("required", []))
            lines.extend(["| Field | Type | Required |", "| --- | --- | --- |"])
            for field_name, field_schema in properties.items():
                marker = "yes" if field_name in required_fields else "no"
                lines.append(f"| `{_escape_markdown_cell(field_name)}` | {_schema_type_label(field_schema)} | {marker} |")
            lines.append("")
    else:
        lines.extend(["No component schemas were synthesized.", ""])
    if state_models:
        lines.extend(["## Client-Side State Models", ""])
        lines.extend(f"- `{model}`" for model in state_models)
        lines.append("")
    return "\n".join(lines)


def export_all(
    document: dict,
    output_dir: Path,
    state_models: list[str] | None = None,
    source_label: str | None = None,
) -> ExportedFiles:
    validate_openapi_document(document)
    files = ExportedFiles(
        json_path=output_dir / "openapi.json",
        yaml_path=output_dir / "openapi.yaml",
        catalog_path=output_dir / "api_catalog.md",
    )
    try:
        output_dir.mkdir(parents=True, exist_ok=True)
        files.json_path.write_text(json.dumps(document, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
        files.yaml_path.write_text(
            yaml.dump(document, Dumper=_IndentedDumper, sort_keys=False, allow_unicode=True, default_flow_style=False),
            encoding="utf-8",
        )
        files.catalog_path.write_text(render_catalog(document, state_models, source_label), encoding="utf-8")
    except (OSError, TypeError, ValueError, yaml.YAMLError) as error:
        raise ExportError(f"Unable to write export files to {output_dir}: {error}") from error
    return files
