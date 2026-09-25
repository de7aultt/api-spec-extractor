import argparse
import json
import sys
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import urlparse

from rich.console import Console
from rich.panel import Panel
from rich.progress import BarColumn, Progress, SpinnerColumn, TextColumn
from rich.table import Table

from config import ConfigurationError, Settings, load_settings
from exporter import ExportError, ExportedFiles, build_openapi_document, export_all
from extractor import ScriptAnalysis, analyze_script, merge_endpoints
from fetcher import FetchError, beautify_script, download_scripts, extract_script_urls, fetch_html
from schema_builder import SchemaBuilder, SchemaSynthesisError, build_heuristic_paths, merge_path_items

EXIT_SUCCESS = 0
EXIT_FAILURE = 1
EXIT_PARTIAL = 2
SCRIPTS_SUBDIRECTORY = "scripts"
EXTRACTION_REPORT_FILENAME = "extraction_report.json"

console = Console()


@dataclass
class ExtractionBundle:
    source_label: str
    analyses: list[ScriptAnalysis] = field(default_factory=list)

    @property
    def endpoints(self) -> list[dict]:
        return merge_endpoints([analysis.endpoints for analysis in self.analyses])

    @property
    def state_models(self) -> list[str]:
        return sorted({model for analysis in self.analyses for model in analysis.state_models})

    @property
    def code_blocks(self) -> list[str]:
        seen: set[str] = set()
        blocks: list[str] = []
        for analysis in self.analyses:
            for block in analysis.code_blocks:
                if block not in seen:
                    seen.add(block)
                    blocks.append(block)
        return blocks


def build_argument_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="api-spec-extractor",
        description="Reconstruct OpenAPI 3.0 specifications from client-side JavaScript bundles.",
    )
    source_group = parser.add_mutually_exclusive_group(required=True)
    source_group.add_argument("--url", metavar="TARGET_URL", help="Web application URL to analyze")
    source_group.add_argument("--file", metavar="LOCAL_JS_PATH", type=Path, help="Local JavaScript file to analyze")
    parser.add_argument("--output", metavar="DIR", type=Path, help="Output directory (overrides OUTPUT_DIR)")
    parser.add_argument("--max-scripts", type=int, help="Maximum number of scripts to download (overrides MAX_SCRIPTS)")
    parser.add_argument("--title", default=None, help="Title for the generated OpenAPI document")
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Run fetching and static extraction without calling the Anthropic API",
    )
    return parser


def collect_from_url(url: str, settings: Settings, output_dir: Path, max_scripts: int) -> ExtractionBundle:
    bundle = ExtractionBundle(source_label=url)
    with console.status(f"Fetching {url}"):
        html = fetch_html(url, timeout=settings.request_timeout)
    script_urls = extract_script_urls(html, url)
    console.print(f"[cyan]Discovered {len(script_urls)} first-party script(s).[/cyan]")
    if len(script_urls) > max_scripts:
        console.print(f"[yellow]Limiting download to the first {max_scripts} script(s).[/yellow]")
        script_urls = script_urls[:max_scripts]
    if not script_urls:
        return bundle
    scripts_dir = output_dir / SCRIPTS_SUBDIRECTORY
    with Progress(
        SpinnerColumn(),
        TextColumn("[progress.description]{task.description}"),
        BarColumn(),
        TextColumn("{task.completed}/{task.total}"),
        console=console,
    ) as progress:
        task_id = progress.add_task("Downloading and beautifying scripts", total=len(script_urls))
        results = download_scripts(
            script_urls,
            scripts_dir,
            timeout=settings.request_timeout,
            on_complete=lambda result: progress.advance(task_id),
        )
    table = Table(title="Script Downloads", show_lines=False)
    table.add_column("Status", no_wrap=True)
    table.add_column("Script URL", overflow="fold")
    table.add_column("Details", overflow="fold")
    for result in results:
        if result.succeeded:
            size_kib = result.path.stat().st_size / 1024
            table.add_row("[green]OK[/green]", result.url, f"{result.path.name} ({size_kib:.1f} KiB)")
        else:
            table.add_row("[red]FAILED[/red]", result.url, result.error or "unknown error")
    console.print(table)
    with console.status("Running static extraction"):
        for result in results:
            if not result.succeeded:
                continue
            content = result.path.read_text(encoding="utf-8", errors="replace")
            bundle.analyses.append(analyze_script(content, result.url))
    return bundle


def collect_from_file(file_path: Path) -> ExtractionBundle:
    if not file_path.is_file():
        raise FileNotFoundError(f"JavaScript file not found: {file_path}")
    bundle = ExtractionBundle(source_label=str(file_path))
    with console.status(f"Beautifying and analyzing {file_path.name}"):
        raw_content = file_path.read_text(encoding="utf-8", errors="replace")
        bundle.analyses.append(analyze_script(beautify_script(raw_content), str(file_path)))
    return bundle


def print_extraction_summary(bundle: ExtractionBundle) -> None:
    endpoints = bundle.endpoints
    table = Table(title=f"Discovered Endpoints ({len(endpoints)})")
    table.add_column("Methods", no_wrap=True)
    table.add_column("Path", overflow="fold")
    table.add_column("Query Params", overflow="fold")
    table.add_column("Hits", justify="right")
    for endpoint in endpoints:
        methods = ", ".join(endpoint["methods"])
        if endpoint["method_inferred"]:
            methods = f"[dim]{methods}?[/dim]"
        table.add_row(methods, endpoint["path"], ", ".join(endpoint["query_params"]) or "-", str(endpoint["occurrences"]))
    console.print(table)
    summary = Table.grid(padding=(0, 2))
    summary.add_row("Scripts analyzed", str(len(bundle.analyses)))
    summary.add_row("Endpoints", str(len(endpoints)))
    summary.add_row("State models", str(len(bundle.state_models)))
    summary.add_row("Code excerpts", str(len(bundle.code_blocks)))
    console.print(Panel(summary, title="Extraction Summary", expand=False))


def write_extraction_report(bundle: ExtractionBundle, output_dir: Path) -> Path:
    report_path = output_dir / EXTRACTION_REPORT_FILENAME
    report = {
        "source": bundle.source_label,
        "scripts": [analysis.source for analysis in bundle.analyses],
        "endpoints": bundle.endpoints,
        "state_models": bundle.state_models,
        "code_excerpt_count": len(bundle.code_blocks),
    }
    output_dir.mkdir(parents=True, exist_ok=True)
    report_path.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    return report_path


def synthesize_specification(bundle: ExtractionBundle, settings: Settings, dry_run: bool) -> tuple[dict, dict, bool]:
    heuristic_paths = build_heuristic_paths(bundle.endpoints)
    if dry_run:
        console.print("[yellow]Dry run: skipping Anthropic synthesis and using heuristic path generation.[/yellow]")
        return heuristic_paths, {}, True
    if not bundle.endpoints and not bundle.code_blocks:
        console.print("[yellow]Nothing to synthesize: no endpoints or API client code were found.[/yellow]")
        return {}, {}, True
    builder = SchemaBuilder(settings)
    with console.status(f"Synthesizing OpenAPI paths with {settings.anthropic_model}") as status:
        result = builder.synthesize(
            bundle.endpoints,
            bundle.state_models,
            bundle.code_blocks,
            notify=lambda message: status.update(message),
        )
    for warning in result.warnings:
        console.print(f"[yellow]Warning: {warning}[/yellow]")
    console.print(
        f"[green]Synthesis completed: {result.batches_succeeded}/{result.batches_total} batch(es), "
        f"{result.input_tokens} input tokens, {result.output_tokens} output tokens.[/green]"
    )
    missing_paths = {path: item for path, item in heuristic_paths.items() if path not in result.paths}
    if missing_paths:
        console.print(f"[cyan]Adding {len(missing_paths)} statically discovered path(s) absent from the synthesis.[/cyan]")
        merge_path_items(result.paths, missing_paths)
    return result.paths, result.components, not result.warnings


def print_export_summary(files: ExportedFiles, report_path: Path, document: dict) -> None:
    operation_count = sum(
        1 for path_item in document["paths"].values() for key in path_item if key in {"get", "post", "put", "delete", "patch", "head", "options", "trace"}
    )
    table = Table(title="Exported Files")
    table.add_column("Artifact")
    table.add_column("Location", overflow="fold")
    table.add_row("OpenAPI JSON", str(files.json_path))
    table.add_row("OpenAPI YAML", str(files.yaml_path))
    table.add_row("API catalog", str(files.catalog_path))
    table.add_row("Extraction report", str(report_path))
    console.print(table)
    console.print(f"[bold green]Generated {len(document['paths'])} path(s) with {operation_count} operation(s).[/bold green]")


def default_title(source_label: str) -> str:
    parsed = urlparse(source_label)
    if parsed.netloc:
        return f"{parsed.netloc} API"
    return f"{Path(source_label).stem} API"


def run(arguments: argparse.Namespace) -> int:
    settings = load_settings()
    output_dir = arguments.output or settings.output_dir
    max_scripts = arguments.max_scripts or settings.max_scripts
    if max_scripts <= 0:
        raise ConfigurationError("--max-scripts must be greater than zero")
    if not arguments.dry_run and not settings.has_api_key:
        raise ConfigurationError("ANTHROPIC_API_KEY is not set; configure it in .env or run with --dry-run")
    if arguments.url:
        bundle = collect_from_url(arguments.url, settings, output_dir, max_scripts)
    else:
        bundle = collect_from_file(arguments.file)
    print_extraction_summary(bundle)
    report_path = write_extraction_report(bundle, output_dir)
    exit_code = EXIT_SUCCESS
    try:
        paths, components, complete = synthesize_specification(bundle, settings, arguments.dry_run)
    except SchemaSynthesisError as error:
        console.print(f"[red]Synthesis failed: {error}[/red]")
        console.print("[yellow]Falling back to heuristic path generation from static extraction.[/yellow]")
        paths, components, complete = build_heuristic_paths(bundle.endpoints), {}, False
    if not complete and not arguments.dry_run:
        exit_code = EXIT_PARTIAL
    document = build_openapi_document(
        paths,
        components,
        title=arguments.title or default_title(bundle.source_label),
        target=arguments.url,
    )
    files = export_all(document, output_dir, bundle.state_models, bundle.source_label)
    print_export_summary(files, report_path, document)
    return exit_code


def main(argv: list[str] | None = None) -> int:
    arguments = build_argument_parser().parse_args(argv)
    try:
        return run(arguments)
    except ConfigurationError as error:
        console.print(f"[red]Configuration error: {error}[/red]")
    except FetchError as error:
        console.print(f"[red]Network error: {error}[/red]")
    except (FileNotFoundError, PermissionError, IsADirectoryError) as error:
        console.print(f"[red]File error: {error}[/red]")
    except ExportError as error:
        console.print(f"[red]Export error: {error}[/red]")
    except OSError as error:
        console.print(f"[red]System error: {error}[/red]")
    except KeyboardInterrupt:
        console.print("[yellow]Interrupted by user.[/yellow]")
    return EXIT_FAILURE


if __name__ == "__main__":
    sys.exit(main())
