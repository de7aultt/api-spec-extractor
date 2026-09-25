import re
import subprocess
import sys
import threading
import uuid
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlparse

from flask import Flask, Response, abort, jsonify, render_template, request, send_file
from werkzeug.exceptions import HTTPException, InternalServerError, NotFound

from config import load_settings

BASE_DIR = Path(__file__).resolve().parent
JOBS_ROOT = BASE_DIR / "output" / "jobs"
MAIN_SCRIPT = BASE_DIR / "main.py"
MAX_LOG_LINES = 2000
ALLOWED_SCHEMES = frozenset({"http", "https"})
DOWNLOAD_FILES = {
    "json": ("openapi.json", "application/json"),
    "yaml": ("openapi.yaml", "application/yaml"),
    "catalog": ("api_catalog.md", "text/markdown"),
}

app = Flask(__name__)

_jobs: dict[str, dict] = {}
_jobs_lock = threading.Lock()


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _normalize_url(raw_url: str) -> str | None:
    candidate = (raw_url or "").strip()
    if not candidate or any(character.isspace() for character in candidate):
        return None
    candidate = re.sub(r"^(?:https?://)?\*\.", "", candidate)
    if "://" not in candidate:
        candidate = f"https://{candidate}"
    parsed = urlparse(candidate)
    if parsed.scheme not in ALLOWED_SCHEMES or not parsed.hostname:
        return None
    if "." not in parsed.hostname and parsed.hostname != "localhost":
        return None
    return candidate


def _job_public_view(job: dict) -> dict:
    return {
        "job_id": job["job_id"],
        "url": job["url"],
        "status": job["status"],
        "created_at": job["created_at"],
        "started_at": job["started_at"],
        "finished_at": job["finished_at"],
        "return_code": job["return_code"],
        "error": job["error"],
        "outputs": job["outputs"],
        "log": job["log"][-MAX_LOG_LINES:],
    }


def _append_log(job_id: str, line: str) -> None:
    with _jobs_lock:
        job = _jobs.get(job_id)
        if job is None:
            return
        job["log"].append(line.rstrip("\n"))
        if len(job["log"]) > MAX_LOG_LINES:
            del job["log"][:-MAX_LOG_LINES]


def _set_job_fields(job_id: str, **fields: object) -> None:
    with _jobs_lock:
        job = _jobs.get(job_id)
        if job is None:
            return
        job.update(fields)


def _detect_outputs(job_dir: Path) -> dict:
    outputs: dict = {}
    for key, (filename, _content_type) in DOWNLOAD_FILES.items():
        candidate = job_dir / filename
        outputs[key] = candidate.exists()
    return outputs


def _build_command(url: str, job_dir: Path, has_api_key: bool) -> list[str]:
    command = [
        sys.executable,
        str(MAIN_SCRIPT),
        "--url",
        url,
        "--output",
        str(job_dir),
    ]
    if not has_api_key:
        command.append("--dry-run")
    return command


def _run_job(job_id: str, url: str, job_dir: Path, has_api_key: bool) -> None:
    _set_job_fields(job_id, status="running", started_at=_now_iso())
    command = _build_command(url, job_dir, has_api_key)
    if not has_api_key:
        _append_log(job_id, "No ANTHROPIC_API_KEY configured; running static heuristic extraction (--dry-run).")
    try:
        process = subprocess.Popen(
            command,
            cwd=str(BASE_DIR),
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            bufsize=1,
        )
    except OSError as error:
        _set_job_fields(
            job_id,
            status="failed",
            finished_at=_now_iso(),
            error=f"Failed to start extraction process: {error}",
        )
        return
    if process.stdout is not None:
        for line in process.stdout:
            _append_log(job_id, line)
    return_code = process.wait()
    outputs = _detect_outputs(job_dir)
    status = "completed" if return_code in (0, 2) else "failed"
    error_message = None if status == "completed" else f"Extraction exited with code {return_code}."
    _set_job_fields(
        job_id,
        status=status,
        finished_at=_now_iso(),
        return_code=return_code,
        outputs=outputs,
        error=error_message,
    )


def _create_job(url: str) -> dict:
    job_id = uuid.uuid4().hex
    job_dir = JOBS_ROOT / job_id
    job_dir.mkdir(parents=True, exist_ok=True)
    job = {
        "job_id": job_id,
        "url": url,
        "status": "running",
        "created_at": _now_iso(),
        "started_at": None,
        "finished_at": None,
        "return_code": None,
        "error": None,
        "outputs": {key: False for key in DOWNLOAD_FILES},
        "job_dir": str(job_dir),
        "log": [],
    }
    with _jobs_lock:
        _jobs[job_id] = job
    settings = load_settings()
    worker = threading.Thread(
        target=_run_job,
        args=(job_id, url, job_dir, settings.has_api_key),
        daemon=True,
    )
    worker.start()
    return job


def _is_api_request() -> bool:
    return request.path.startswith("/api/")


@app.errorhandler(404)
def handle_not_found(error: NotFound) -> Response | HTTPException:
    if not _is_api_request():
        return error
    response = jsonify({"error": "Resource not found.", "path": request.path})
    response.status_code = 404
    return response


@app.errorhandler(500)
def handle_internal_error(error: InternalServerError) -> Response | HTTPException:
    if not _is_api_request():
        return error
    original = getattr(error, "original_exception", None)
    message = f"Internal server error: {original}" if original is not None else "Internal server error."
    response = jsonify({"error": message})
    response.status_code = 500
    return response


@app.get("/")
def index() -> str:
    return render_template("index.html")


@app.get("/api/targets")
def api_targets() -> Response:
    from radar import RadarError, fetch_or_load_targets, filter_targets

    search_query = request.args.get("search", "", type=str)
    refresh = request.args.get("refresh", "false", type=str).lower() in {"1", "true", "yes"}
    try:
        min_bounty = float(request.args.get("bounty_min", "0", type=str) or 0)
    except ValueError:
        min_bounty = 0.0
    try:
        min_response = float(request.args.get("response_min", "0", type=str) or 0)
    except ValueError:
        min_response = 0.0
    try:
        programs = fetch_or_load_targets(force_refresh=refresh)
    except RadarError as error:
        return jsonify({"error": str(error), "targets": []}), 502
    targets = filter_targets(
        programs,
        min_bounty=min_bounty,
        web_only=True,
        search_query=search_query,
        min_response_rate=min_response,
    )
    return jsonify({"count": len(targets), "targets": targets})


@app.post("/api/scan")
def api_scan() -> Response:
    payload = request.get_json(silent=True) or {}
    url = _normalize_url(str(payload.get("url", "")))
    if url is None:
        return jsonify({"error": "A valid http(s) URL is required."}), 400
    job = _create_job(url)
    return jsonify({"job_id": job["job_id"], "status": job["status"], "url": job["url"]}), 202


@app.get("/api/scan")
def api_scan_list() -> Response:
    with _jobs_lock:
        jobs = [_job_public_view(job) for job in _jobs.values()]
    jobs.sort(key=lambda item: item["created_at"], reverse=True)
    return jsonify({"jobs": jobs})


@app.get("/api/scan/<job_id>")
def api_scan_status(job_id: str) -> Response:
    with _jobs_lock:
        job = _jobs.get(job_id)
        view = _job_public_view(job) if job is not None else None
    if view is None:
        return jsonify({"error": "Unknown job id."}), 404
    return jsonify(view)


@app.get("/api/openapi/<job_id>")
def api_openapi(job_id: str) -> Response:
    with _jobs_lock:
        job = _jobs.get(job_id)
        job_dir = job["job_dir"] if job is not None else None
    if job_dir is None:
        abort(404)
    spec_path = Path(job_dir) / "openapi.json"
    if not spec_path.exists():
        abort(404)
    return send_file(spec_path, mimetype="application/json")


@app.get("/download/<job_id>/<file_type>")
def download(job_id: str, file_type: str) -> Response:
    if file_type not in DOWNLOAD_FILES:
        abort(404)
    with _jobs_lock:
        job = _jobs.get(job_id)
        job_dir = job["job_dir"] if job is not None else None
    if job_dir is None:
        abort(404)
    filename, content_type = DOWNLOAD_FILES[file_type]
    file_path = Path(job_dir) / filename
    if not file_path.exists():
        abort(404)
    return send_file(file_path, mimetype=content_type, as_attachment=True, download_name=filename)


@app.get("/swagger/<job_id>")
def swagger(job_id: str) -> str:
    with _jobs_lock:
        exists = job_id in _jobs
    if not exists:
        abort(404)
    return render_template("swagger.html", job_id=job_id)


def create_app() -> Flask:
    JOBS_ROOT.mkdir(parents=True, exist_ok=True)
    return app


def main() -> int:
    create_app()
    host = "127.0.0.1"
    port = 5000
    print(f"Starting api-spec-extractor dashboard on http://{host}:{port}")
    app.run(host=host, port=port, debug=False, threaded=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
