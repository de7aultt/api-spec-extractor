import json
from pathlib import Path

import pytest

import main as cli
from conftest import APP_CHUNK, MINIFIED_ZIGGY_CHUNK, VITE_MANIFEST, ZIGGY_CONFIG, build_laravel_page, build_next_page
from extractor import (
    JavaScriptLiteralError,
    analyze_html_document,
    analyze_script,
    extract_endpoints,
    extract_inertia_page,
    extract_next_data,
    extract_route_calls,
    extract_ziggy_routes,
    merge_endpoints,
    next_data_route,
    normalize_ziggy_uri,
    parse_javascript_literal,
    resolve_route_calls,
    ziggy_routes_to_endpoints,
)
from fetcher import (
    discover_manifests,
    extract_inline_module_imports,
    extract_inline_scripts,
    extract_preload_urls,
    extract_script_urls,
    parse_vite_manifest,
    prioritize_script_urls,
    render_inline_scripts,
    save_inline_scripts,
)


def _endpoint_map(endpoints: list[dict]) -> dict[str, list[str]]:
    return {endpoint["path"]: endpoint["methods"] for endpoint in endpoints}


def test_parse_vite_manifest_resolves_chunks_relative_to_build_directory():
    discovery = parse_vite_manifest(VITE_MANIFEST, "https://app.test/build/manifest.json")
    assert discovery is not None
    assert discovery.bundler == "Vite"
    assert discovery.chunk_urls[0] == "https://app.test/build/assets/app-4f2a1c.js"
    assert set(discovery.chunk_urls) == {
        "https://app.test/build/assets/app-4f2a1c.js",
        "https://app.test/build/assets/vendor-9b8c7d.js",
        "https://app.test/build/assets/Admin-77ee66.js",
    }
    assert discovery.entry_urls == ["https://app.test/build/assets/app-4f2a1c.js"]


def test_parse_vite_manifest_handles_dot_vite_directory_and_rejects_pwa_manifest():
    discovery = parse_vite_manifest(VITE_MANIFEST, "https://app.test/.vite/manifest.json")
    assert discovery.chunk_urls[0] == "https://app.test/assets/app-4f2a1c.js"
    assert parse_vite_manifest({"name": "Demo", "icons": []}, "https://app.test/manifest.json") is None
    assert parse_vite_manifest([], "https://app.test/manifest.json") is None


def test_inline_scripts_are_extracted_and_rendered_as_virtual_script(tmp_path: Path):
    html = build_laravel_page() + build_next_page()
    scripts = extract_inline_scripts(html)
    assert any("Ziggy" in script.content for script in scripts)
    assert any(script.element_id == "__NEXT_DATA__" and script.is_json for script in scripts)
    assert all("googletagmanager" not in script.content for script in scripts)
    saved = save_inline_scripts(scripts, tmp_path)
    assert saved.name == "inline_scripts.js"
    rendered = saved.read_text(encoding="utf-8")
    assert "const __NEXT_DATA__ =" in rendered
    assert render_inline_scripts(scripts) == rendered
    assert save_inline_scripts([], tmp_path / "empty") is None


def test_preload_links_and_inline_module_imports_are_discovered():
    html = build_laravel_page() + '<script type="module">import "/build/assets/boot-1.js"; import("./lazy-2.js")</script>'
    assert extract_preload_urls(html, "https://app.test/") == ["https://app.test/build/assets/vendor-9b8c7d.js"]
    imports = extract_inline_module_imports(extract_inline_scripts(html), "https://app.test/")
    assert imports == ["https://app.test/build/assets/boot-1.js", "https://app.test/lazy-2.js"]


def test_prioritize_script_urls_deduplicates_and_caps():
    selected, total = prioritize_script_urls([["a", "b"], ["b", "c"], ["d"]], 3)
    assert selected == ["a", "b", "c"]
    assert total == 4


def test_extract_script_urls_is_backwards_compatible():
    urls = extract_script_urls(build_laravel_page(), "https://app.test/")
    assert urls == ["https://app.test/build/assets/app-4f2a1c.js"]


def test_ziggy_routes_are_parsed_from_escaped_json_in_html():
    routes = {route["name"]: route for route in extract_ziggy_routes(build_laravel_page())}
    assert set(routes) == set(ZIGGY_CONFIG["routes"])
    assert routes["users.update"]["path"] == "/api/users/{user}"
    assert routes["users.update"]["methods"] == ["PUT", "PATCH"]
    assert routes["login"]["methods"] == ["GET"]
    assert routes["posts.comments"]["path"] == "/posts/{post}/comments/{comment}"
    assert routes["posts.comments"]["optional_parameters"] == ["comment"]


def test_ziggy_routes_are_parsed_from_minified_javascript_object():
    routes = {route["name"]: route for route in extract_ziggy_routes(MINIFIED_ZIGGY_CHUNK)}
    assert routes["admin.reports.export"]["path"] == "/admin/reports/{report}/export"
    assert routes["admin.reports.export"]["methods"] == ["POST"]
    assert routes["admin.dashboard"]["path"] == "/admin/dashboard"


def test_ziggy_routes_are_parsed_from_json_parse_assignment():
    content = "window.Ziggy = JSON.parse('" + json.dumps(ZIGGY_CONFIG) + "');"
    assert len(extract_ziggy_routes(content)) == len(ZIGGY_CONFIG["routes"])


def test_non_ziggy_objects_are_ignored():
    assert extract_ziggy_routes("const Ziggy = {url: 'x', port: null, routes: {}};") == []
    assert extract_ziggy_routes("const config = {url: 'x', port: 80, other: true};") == []


def test_normalize_ziggy_uri_applies_base_path_prefix():
    assert normalize_ziggy_uri("api/users/{user?}") == "/api/users/{user}"
    assert normalize_ziggy_uri("/", "https://app.test/portal") == "/portal"
    assert normalize_ziggy_uri("dashboard", "https://app.test/portal/") == "/portal/dashboard"


def test_javascript_literal_parser_supports_relaxed_syntax():
    parsed = parse_javascript_literal("{a:1,'b':'it\\'s',c:[!0,!1,void 0,],d:.5,0x10:\"hex\",}")
    assert parsed == {"a": 1, "b": "it's", "c": [True, False, None], "d": 0.5, "0x10": "hex"}
    with pytest.raises(JavaScriptLiteralError):
        parse_javascript_literal("{a: someVariable}")


def test_route_calls_resolve_to_ziggy_uris_with_explicit_methods():
    calls = extract_route_calls(APP_CHUNK)
    assert [call["name"] for call in calls] == ["users.store", "users.destroy", "does.not.exist"]
    assert calls[0]["method"] == "POST"
    assert calls[1]["method"] == "DELETE"
    routes = extract_ziggy_routes(build_laravel_page())
    endpoints, unresolved = resolve_route_calls(calls, routes)
    assert _endpoint_map(endpoints) == {"/api/users": ["POST"], "/api/users/{user}": ["DELETE"]}
    assert unresolved == ["does.not.exist"]


def test_ziggy_endpoints_merge_methods_and_route_names():
    endpoints = ziggy_routes_to_endpoints(extract_ziggy_routes(build_laravel_page()))
    by_path = {endpoint["path"]: endpoint for endpoint in endpoints}
    assert by_path["/api/users/{user}"]["methods"] == ["GET", "PUT", "DELETE", "PATCH"]
    assert set(by_path["/api/users/{user}"]["route_names"]) == {"users.show", "users.update", "users.destroy"}
    assert by_path["/api/users/{user}"]["path_params"] == ["user"]


def test_merge_endpoints_remains_compatible_with_sprint_one_entries():
    legacy = extract_endpoints("axios.get('/api/users')")
    merged = merge_endpoints([legacy, ziggy_routes_to_endpoints(extract_ziggy_routes(build_laravel_page()))])
    users = next(endpoint for endpoint in merged if endpoint["path"] == "/api/users")
    assert users["methods"] == ["GET", "POST"]
    assert users["sources"] == ["ziggy"]


def test_inertia_page_extraction_and_state_models():
    page = extract_inertia_page(build_laravel_page())
    assert page["component"] == "Users/Index"
    analysis = analyze_html_document(build_laravel_page(), "index.html")
    assert "inertia.props.auth.user" in analysis.state_models
    assert "inertia.props.auth.user.email" in analysis.state_models
    assert "/api/users/export" in _endpoint_map(analysis.endpoints)
    frameworks = [detection.framework for detection in analysis.detections]
    assert frameworks.count("Laravel Ziggy") == 2
    assert "Inertia.js" in frameworks
    assert len(analysis.ziggy_routes) == len(ZIGGY_CONFIG["routes"])


def test_inertia_page_supports_script_payload_variant():
    html = '<script data-page="app" type="application/json">{"component":"Home","props":{"user":null}}</script>'
    assert extract_inertia_page(html)["component"] == "Home"
    assert extract_inertia_page('<div id="app" data-page="not json"></div>') is None


def test_next_data_extraction_and_data_route():
    data = extract_next_data(build_next_page())
    assert data["buildId"] == "b1d2"
    assert next_data_route(data) == "/_next/data/b1d2/products/{slug}.json"
    assert next_data_route({"buildId": "x", "page": "/"}) == "/_next/data/x/index.json"
    analysis = analyze_html_document(build_next_page(), "next.html")
    endpoints = _endpoint_map(analysis.endpoints)
    assert endpoints["/_next/data/b1d2/products/{slug}.json"] == ["GET"]
    assert "/api/v2/catalog" in endpoints
    assert "next.pageProps.product.title" in analysis.state_models
    assert [detection.framework for detection in analysis.detections] == ["Next.js"]


def test_document_without_frameworks_reports_nothing():
    analysis = analyze_html_document("<html><body><p>Hello</p></body></html>", "plain.html")
    assert analysis.detections == []
    assert analysis.endpoints == []
    assert extract_next_data('<script id="__NEXT_DATA__">{broken</script>') is None


def test_analyze_script_collects_ziggy_and_route_calls():
    analysis = analyze_script(MINIFIED_ZIGGY_CHUNK + APP_CHUNK, "bundle.js")
    assert {route["name"] for route in analysis.ziggy_routes} == {"admin.dashboard", "admin.reports.export"}
    assert len(analysis.route_calls) == 3
    assert "/api/stats" in _endpoint_map(analysis.endpoints)


def test_discover_manifests_over_http(laravel_site: Path, serve_directory):
    base_url = serve_directory(laravel_site)
    discoveries = discover_manifests(base_url, timeout=5)
    assert len(discoveries) == 1
    assert discoveries[0].manifest_path == "/build/manifest.json"
    assert discoveries[0].chunk_count == 3


def test_cli_url_dry_run_end_to_end(laravel_site: Path, serve_directory, tmp_path: Path, capsys):
    base_url = serve_directory(laravel_site)
    output_dir = tmp_path / "out"
    exit_code = cli.main(["--url", base_url, "--dry-run", "--output", str(output_dir)])
    captured = capsys.readouterr().out
    assert exit_code == 0
    assert "Framework & Manifest Discovery" in captured
    assert "3 chunks in /build/manifest.json" in captured
    assert "routes in HTML" in captured
    assert "Total combined routes" in captured
    assert (output_dir / "scripts" / "inline_scripts.js").is_file()
    report = json.loads((output_dir / "extraction_report.json").read_text(encoding="utf-8"))
    paths = {endpoint["path"]: endpoint["methods"] for endpoint in report["endpoints"]}
    assert paths["/api/users/{user}"] == ["GET", "PUT", "DELETE", "PATCH"]
    assert paths["/admin/reports/{report}/export"] == ["POST"]
    assert paths["/api/session/ping"] == ["POST"]
    assert paths["/api/stats"] == ["GET"]
    assert report["unresolved_route_names"] == ["does.not.exist"]
    frameworks = {entry["framework"] for entry in report["frameworks"]}
    assert {"Vite", "Laravel Ziggy", "Inertia.js", "Inline scripts"} <= frameworks
    spec = json.loads((output_dir / "openapi.json").read_text(encoding="utf-8"))
    assert spec["openapi"].startswith("3.0")
    assert "/api/users/{user}" in spec["paths"]


def test_cli_max_scripts_limits_downloads(laravel_site: Path, serve_directory, tmp_path: Path, capsys):
    base_url = serve_directory(laravel_site)
    exit_code = cli.main(["--url", base_url, "--dry-run", "--output", str(tmp_path / "out"), "--max-scripts", "1"])
    assert exit_code == 0
    assert "Limiting download to 1 of 3 script(s)" in capsys.readouterr().out


def test_cli_file_mode_accepts_html_and_javascript(tmp_path: Path):
    html_path = tmp_path / "page.html"
    html_path.write_text(build_next_page(), encoding="utf-8")
    assert cli.main(["--file", str(html_path), "--dry-run", "--output", str(tmp_path / "html_out")]) == 0
    spec = json.loads((tmp_path / "html_out" / "openapi.json").read_text(encoding="utf-8"))
    assert "/_next/data/b1d2/products/{slug}.json" in spec["paths"]
    js_path = tmp_path / "bundle.js"
    js_path.write_text(MINIFIED_ZIGGY_CHUNK, encoding="utf-8")
    assert cli.main(["--file", str(js_path), "--dry-run", "--output", str(tmp_path / "js_out")]) == 0
    spec = json.loads((tmp_path / "js_out" / "openapi.json").read_text(encoding="utf-8"))
    assert "/admin/dashboard" in spec["paths"]


def test_cli_reports_unreachable_targets(tmp_path: Path, serve_directory):
    serve_directory(tmp_path)
    assert cli.main(["--url", "http://127.0.0.1:9/", "--dry-run", "--output", str(tmp_path / "out")]) == 1
    assert cli.main(["--file", str(tmp_path / "missing.js"), "--dry-run"]) == 1
