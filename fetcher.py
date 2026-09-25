import hashlib
import re
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
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
