import functools
import html
import json
import os
import sys
import threading
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

ZIGGY_CONFIG = {
    "url": "http://localhost",
    "port": None,
    "defaults": {},
    "routes": {
        "login": {"uri": "login", "methods": ["GET", "HEAD"]},
        "users.index": {"uri": "api/users", "methods": ["GET", "HEAD"]},
        "users.store": {"uri": "api/users", "methods": ["POST"]},
        "users.show": {"uri": "api/users/{user}", "methods": ["GET", "HEAD"], "parameters": ["user"]},
        "users.update": {"uri": "api/users/{user}", "methods": ["PUT", "PATCH"], "parameters": ["user"]},
        "users.destroy": {"uri": "api/users/{user}", "methods": ["DELETE"], "parameters": ["user"]},
        "posts.comments": {"uri": "posts/{post}/comments/{comment?}", "methods": ["GET", "HEAD"]},
    },
}

MINIFIED_ZIGGY_CHUNK = (
    'const e={url:"http://localhost",port:null,defaults:{},routes:{"admin.dashboard":'
    '{uri:"admin/dashboard",methods:["GET","HEAD"]},"admin.reports.export":{uri:"admin/reports/{report}/export",'
    'methods:["POST"],wheres:{report:"[0-9]+"}}}};export{e as Z};'
)

APP_CHUNK = """
import axios from "axios";
export async function saveUser(data) { return axios.post(route('users.store'), data); }
export function removeUser(id) { return router.delete(route("users.destroy", id)); }
export function openMissing() { return route('does.not.exist'); }
export const loadStats = () => fetch('/api/stats?range=7d');
"""

VITE_MANIFEST = {
    "resources/js/app.js": {"file": "assets/app-4f2a1c.js", "isEntry": True, "src": "resources/js/app.js",
                            "imports": ["_vendor-9b8c7d.js"], "css": ["assets/app-11aa22.css"]},
    "_vendor-9b8c7d.js": {"file": "assets/vendor-9b8c7d.js"},
    "resources/js/Pages/Admin.vue": {"file": "assets/Admin-77ee66.js", "isDynamicEntry": True,
                                     "src": "resources/js/Pages/Admin.vue"},
    "resources/css/app.css": {"file": "assets/app-11aa22.css", "src": "resources/css/app.css"},
}

PWA_MANIFEST = {"name": "Demo", "short_name": "Demo", "icons": [{"src": "/icon.png", "sizes": "192x192"}]}


def build_laravel_page() -> str:
    inertia_page = {
        "component": "Users/Index",
        "props": {
            "auth": {"user": {"id": 1, "name": "Ada", "email": "ada@example.com"}},
            "filters": {"search": "", "page": 1},
            "endpoints": {"export": "/api/users/export"},
            "ziggy": {**ZIGGY_CONFIG, "location": "http://localhost/users"},
        },
        "url": "/users",
        "version": "abc123",
    }
    ziggy_script = "const Ziggy = " + json.dumps(ZIGGY_CONFIG).replace("/", "\\/") + ";"
    return f"""<!DOCTYPE html>
<html>
<head>
<script type="text/javascript">{ziggy_script}</script>
<link rel="modulepreload" href="/build/assets/vendor-9b8c7d.js">
<script type="module" src="/build/assets/app-4f2a1c.js"></script>
<script src="https://www.googletagmanager.com/gtag/js?id=G-1"></script>
<script>window.dataLayer = window.dataLayer || []; fetch('/api/session/ping', {{ method: 'POST' }});</script>
</head>
<body>
<div id="app" data-page="{html.escape(json.dumps(inertia_page), quote=True)}"></div>
</body>
</html>
"""


def build_next_page() -> str:
    next_data = {
        "props": {"pageProps": {"product": {"id": "p-1", "title": "Lamp"}, "apiBase": "/api/v2/catalog"}},
        "page": "/products/[slug]",
        "query": {"slug": "lamp"},
        "buildId": "b1d2",
        "isFallback": False,
        "gssp": True,
    }
    return f"""<!DOCTYPE html>
<html><head><script src="/_next/static/chunks/main.js"></script></head>
<body><div id="__next"></div>
<script id="__NEXT_DATA__" type="application/json">{json.dumps(next_data)}</script>
</body></html>
"""


@pytest.fixture()
def laravel_site(tmp_path: Path) -> Path:
    root = tmp_path / "laravel"
    assets = root / "build" / "assets"
    assets.mkdir(parents=True)
    (root / "index.html").write_text(build_laravel_page(), encoding="utf-8")
    (root / "build" / "manifest.json").write_text(json.dumps(VITE_MANIFEST), encoding="utf-8")
    (root / "manifest.json").write_text(json.dumps(PWA_MANIFEST), encoding="utf-8")
    (assets / "app-4f2a1c.js").write_text(APP_CHUNK, encoding="utf-8")
    (assets / "vendor-9b8c7d.js").write_text("export const v=1;", encoding="utf-8")
    (assets / "Admin-77ee66.js").write_text(MINIFIED_ZIGGY_CHUNK, encoding="utf-8")
    return root


class _QuietHandler(SimpleHTTPRequestHandler):
    def log_message(self, format, *args):
        return


@pytest.fixture()
def serve_directory():
    servers = []
    previous_no_proxy = os.environ.get("NO_PROXY")
    os.environ["NO_PROXY"] = ",".join(filter(None, [previous_no_proxy, "127.0.0.1", "localhost"]))

    def start(directory: Path) -> str:
        handler = functools.partial(_QuietHandler, directory=str(directory))
        server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        servers.append(server)
        return f"http://127.0.0.1:{server.server_address[1]}/"

    yield start
    for server in servers:
        server.shutdown()
        server.server_close()
    if previous_no_proxy is None:
        os.environ.pop("NO_PROXY", None)
    else:
        os.environ["NO_PROXY"] = previous_no_proxy
