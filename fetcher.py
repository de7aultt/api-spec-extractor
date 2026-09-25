import hashlib
import json
import re
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import urldefrag, urljoin, urlparse

import jsbeautifier
import requests
from bs4 import BeautifulSoup

from config import DEFAULT_REQUEST_TIMEOUT

BROWSER_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
}

TRACKER_PATTERNS = (
    "google-analytics.com",
    "googletagmanager.com",
    "googleadservices.com",
    "doubleclick.net",
    "gtag/js",
    "connect.facebook.net",
    "facebook.net/en_us/fbevents",
    "fbevents.js",
    "mc.yandex.ru",
    "yandex.ru/metrika",
    "metrika/tag.js",
    "sentry.io",
    "sentry-cdn.com",
    "browser.sentry-cdn",
    "hotjar.com",
    "static.hotjar",
)

MAX_FILENAME_STEM_LENGTH = 80
DEFAULT_DOWNLOAD_WORKERS = 6
MANIFEST_CANDIDATE_PATHS = (
    "/build/manifest.json",
    "/.vite/manifest.json",
    "/build/.vite/manifest.json",
    "/manifest.json",
)
SCRIPT_ASSET_SUFFIXES = (".js", ".mjs")
INLINE_SCRIPTS_FILENAME = "inline_scripts.js"
JSON_SCRIPT_TYPES = frozenset({"application/json", "application/ld+json", "importmap", "speculationrules"})
NON_SCRIPT_TYPES = frozenset({"text/template", "text/x-template", "text/html", "text/css", "text/plain"})
INLINE_MODULE_IMPORT_PATTERN = re.compile(
    r"""(?:\bimport\s*(?:[\w$*{},\s]+\s*from\s*)?|\bimport\s*\(\s*)(['"`])(?P<path>[^'"`\s]+?\.m?js(?:\?[^'"`\s]*)?)\1"""
)


class FetchError(RuntimeError):
    pass


@dataclass(frozen=True)
class DownloadResult:
    url: str
    path: Path | None
    error: str | None

    @property
    def succeeded(self) -> bool:
        return self.path is not None


def _create_session() -> requests.Session:
    session = requests.Session()
    session.headers.update(BROWSER_HEADERS)
    return session


def _get(url: str, timeout: float) -> requests.Response:
    try:
        with _create_session() as session:
            response = session.get(url, timeout=timeout)
            response.raise_for_status()
            return response
    except requests.exceptions.Timeout as error:
        raise FetchError(f"Request to {url} timed out after {timeout} seconds") from error
    except requests.exceptions.HTTPError as error:
        status = error.response.status_code if error.response is not None else "unknown"
        raise FetchError(f"Request to {url} failed with HTTP status {status}") from error
    except requests.exceptions.RequestException as error:
        raise FetchError(f"Request to {url} failed: {error}") from error


def fetch_html(url: str, timeout: float = DEFAULT_REQUEST_TIMEOUT) -> str:
    parsed = urlparse(url)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise FetchError(f"Invalid target URL: {url}")
    return _get(url, timeout).text


def is_tracker_script(url: str) -> bool:
    lowered = url.lower()
    return any(pattern in lowered for pattern in TRACKER_PATTERNS)


def extract_script_urls(html: str, base_url: str) -> list[str]:
    soup = BeautifulSoup(html, "html.parser")
    base_tag = soup.find("base", href=True)
    effective_base = urljoin(base_url, base_tag["href"]) if base_tag else base_url
    discovered: list[str] = []
    seen: set[str] = set()
    for script_tag in soup.find_all("script", src=True):
        source = script_tag["src"].strip()
        if not source or source.startswith(("data:", "javascript:")):
            continue
        absolute_url, _ = urldefrag(urljoin(effective_base, source))
        if urlparse(absolute_url).scheme not in {"http", "https"}:
            continue
        if is_tracker_script(absolute_url) or absolute_url in seen:
            continue
        seen.add(absolute_url)
        discovered.append(absolute_url)
    return discovered


def _build_script_filename(url: str) -> str:
    parsed = urlparse(url)
    raw_name = Path(parsed.path).name or "index"
    stem = re.sub(r"[^A-Za-z0-9._-]", "_", Path(raw_name).stem)[:MAX_FILENAME_STEM_LENGTH] or "script"
    digest = hashlib.sha1(url.encode("utf-8")).hexdigest()[:10]
    return f"{stem}.{digest}.js"


def beautify_script(source: str) -> str:
    options = jsbeautifier.default_options()
    options.indent_size = 2
    options.preserve_newlines = False
    try:
        return jsbeautifier.beautify(source, options)
    except Exception:
        return source


def download_and_format_script(
    url: str,
    output_dir: Path,
    timeout: float = DEFAULT_REQUEST_TIMEOUT,
) -> Path:
    response = _get(url, timeout)
    response.encoding = response.encoding or "utf-8"
    formatted = beautify_script(response.text)
    try:
        output_dir.mkdir(parents=True, exist_ok=True)
        target_path = output_dir / _build_script_filename(url)
        target_path.write_text(formatted, encoding="utf-8")
    except OSError as error:
        raise FetchError(f"Unable to save script from {url}: {error}") from error
    return target_path


def download_scripts(
    urls: list[str],
    output_dir: Path,
    timeout: float = DEFAULT_REQUEST_TIMEOUT,
    max_workers: int = DEFAULT_DOWNLOAD_WORKERS,
    on_complete=None,
) -> list[DownloadResult]:
    if not urls:
        return []
    results: dict[str, DownloadResult] = {}
    worker_count = max(1, min(max_workers, len(urls)))
    with ThreadPoolExecutor(max_workers=worker_count) as executor:
        futures = {
            executor.submit(download_and_format_script, url, output_dir, timeout): url for url in urls
        }
        for future in as_completed(futures):
            url = futures[future]
            try:
                result = DownloadResult(url=url, path=future.result(), error=None)
            except FetchError as error:
                result = DownloadResult(url=url, path=None, error=str(error))
            results[url] = result
            if on_complete is not None:
                on_complete(result)
    return [results[url] for url in urls]


@dataclass(frozen=True)
class ManifestDiscovery:
    manifest_url: str
    manifest_path: str
    bundler: str
    chunk_urls: list[str] = field(default_factory=list)
    entry_urls: list[str] = field(default_factory=list)

    @property
    def chunk_count(self) -> int:
        return len(self.chunk_urls)


@dataclass(frozen=True)
class InlineScript:
    index: int
    content: str
    script_type: str
    element_id: str | None

    @property
    def is_json(self) -> bool:
        return self.script_type in JSON_SCRIPT_TYPES or self.script_type.endswith("+json")


def _origin(url: str) -> str:
    parsed = urlparse(url)
    return f"{parsed.scheme}://{parsed.netloc}"


def _manifest_asset_base(manifest_url: str) -> str:
    directory_url = urljoin(manifest_url, ".")
    if directory_url.rstrip("/").endswith("/.vite"):
        return urljoin(directory_url, "..")
    return directory_url


def _is_script_asset(path: str) -> bool:
    return urlparse(path).path.lower().endswith(SCRIPT_ASSET_SUFFIXES)


def parse_vite_manifest(payload: object, manifest_url: str) -> ManifestDiscovery | None:
    if not isinstance(payload, dict) or not payload:
        return None
    entries = [entry for entry in payload.values() if isinstance(entry, dict) and isinstance(entry.get("file"), str)]
    if not entries:
        return None
    asset_base = _manifest_asset_base(manifest_url)
    ordered_entries = sorted(entries, key=lambda entry: (not entry.get("isEntry", False), entry.get("isDynamicEntry", False)))
    chunk_urls: list[str] = []
    entry_urls: list[str] = []
    for entry in ordered_entries:
        file_path = entry["file"].lstrip("/")
        if not _is_script_asset(file_path):
            continue
        absolute_url = urljoin(asset_base, file_path)
        if absolute_url in chunk_urls:
            continue
        chunk_urls.append(absolute_url)
        if entry.get("isEntry"):
            entry_urls.append(absolute_url)
    if not chunk_urls:
        return None
    return ManifestDiscovery(
        manifest_url=manifest_url,
        manifest_path=urlparse(manifest_url).path,
        bundler="Vite",
        chunk_urls=chunk_urls,
        entry_urls=entry_urls,
    )


def discover_manifests(
    base_url: str,
    timeout: float = DEFAULT_REQUEST_TIMEOUT,
    candidate_paths: tuple[str, ...] = MANIFEST_CANDIDATE_PATHS,
) -> list[ManifestDiscovery]:
    roots = (urljoin(base_url, "./"), f"{_origin(base_url)}/")
    candidate_urls: list[str] = []
    for candidate_path in candidate_paths:
        for root in roots:
            candidate_url = urljoin(root, candidate_path.lstrip("/"))
            if candidate_url not in candidate_urls:
                candidate_urls.append(candidate_url)
    discoveries: list[ManifestDiscovery] = []
    known_chunks: set[str] = set()
    for candidate_url in candidate_urls:
        try:
            response = _get(candidate_url, timeout)
            payload = json.loads(response.text)
        except (FetchError, ValueError):
            continue
        discovery = parse_vite_manifest(payload, candidate_url)
        if discovery is None or set(discovery.chunk_urls) <= known_chunks:
            continue
        known_chunks.update(discovery.chunk_urls)
        discoveries.append(discovery)
    return discoveries


def extract_preload_urls(html: str, base_url: str) -> list[str]:
    soup = BeautifulSoup(html, "html.parser")
    discovered: list[str] = []
    for link_tag in soup.find_all("link", href=True):
        relations = {relation.lower() for relation in (link_tag.get("rel") or [])}
        is_module_preload = "modulepreload" in relations
        is_script_preload = "preload" in relations and (link_tag.get("as") or "").lower() == "script"
        if not (is_module_preload or is_script_preload):
            continue
        absolute_url, _ = urldefrag(urljoin(base_url, link_tag["href"].strip()))
        if urlparse(absolute_url).scheme in {"http", "https"} and not is_tracker_script(absolute_url):
            if absolute_url not in discovered:
                discovered.append(absolute_url)
    return discovered


def extract_inline_scripts(html: str) -> list[InlineScript]:
    soup = BeautifulSoup(html, "html.parser")
    scripts: list[InlineScript] = []
    for script_tag in soup.find_all("script"):
        if script_tag.get("src"):
            continue
        script_type = (script_tag.get("type") or "text/javascript").strip().lower()
        if script_type in NON_SCRIPT_TYPES:
            continue
        content = (script_tag.string or script_tag.get_text() or "").strip()
        if not content:
            continue
        scripts.append(
            InlineScript(
                index=len(scripts) + 1,
                content=content,
                script_type=script_type,
                element_id=script_tag.get("id"),
            )
        )
    return scripts


def extract_inline_module_imports(inline_scripts: list[InlineScript], base_url: str) -> list[str]:
    discovered: list[str] = []
    for script in inline_scripts:
        if script.is_json:
            continue
        for match in INLINE_MODULE_IMPORT_PATTERN.finditer(script.content):
            absolute_url, _ = urldefrag(urljoin(base_url, match.group("path")))
            if urlparse(absolute_url).scheme not in {"http", "https"} or is_tracker_script(absolute_url):
                continue
            if absolute_url not in discovered:
                discovered.append(absolute_url)
    return discovered


def render_inline_scripts(inline_scripts: list[InlineScript]) -> str:
    rendered_parts = []
    for script in inline_scripts:
        body = script.content if script.is_json else beautify_script(script.content)
        if script.is_json:
            label = script.element_id or f"inline_json_{script.index}"
            safe_label = re.sub(r"[^A-Za-z0-9_$]", "_", label)
            body = f"const {safe_label} = {body};"
        rendered_parts.append(body.rstrip().rstrip(";") + ";")
    return "\n\n".join(rendered_parts) + "\n"


def save_inline_scripts(inline_scripts: list[InlineScript], output_dir: Path) -> Path | None:
    if not inline_scripts:
        return None
    try:
        output_dir.mkdir(parents=True, exist_ok=True)
        target_path = output_dir / INLINE_SCRIPTS_FILENAME
        target_path.write_text(render_inline_scripts(inline_scripts), encoding="utf-8")
    except OSError as error:
        raise FetchError(f"Unable to save inline scripts: {error}") from error
    return target_path


def prioritize_script_urls(url_groups: list[list[str]], max_scripts: int) -> tuple[list[str], int]:
    ordered: list[str] = []
    seen: set[str] = set()
    for group in url_groups:
        for url in group:
            if url not in seen:
                seen.add(url)
                ordered.append(url)
    return ordered[:max_scripts], len(ordered)
