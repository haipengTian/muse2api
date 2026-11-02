"""Offline checks for the read-only session diagnostic tool."""
import copy
import importlib.util
import io
import json
import os
import sys
import tempfile
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from contextlib import redirect_stdout
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
TOOL = ROOT / "tools/diagnose_session.py"
assert TOOL.is_file(), "missing automatic session diagnostic tool"
spec = importlib.util.spec_from_file_location("diagnose_session", TOOL)
tool = importlib.util.module_from_spec(spec)
spec.loader.exec_module(tool)

from config import Config

accounts = [
    {"id": "older", "enabled": True, "created_at": 10, "cookies": {"hatch_sess": "old"}},
    {"id": "newest", "enabled": True, "created_at": 20, "cookies": {
        "hatch_sess": "DO_NOT_PRINT_SESSION", "hatch_vml": "DO_NOT_PRINT_" + "x" * 5000}},
    {"id": "disabled", "enabled": False, "created_at": 30, "cookies": {"hatch_sess": "off"}},
]
original = copy.deepcopy(accounts)
assert tool.select_account(accounts)["id"] == "newest"
assert tool.select_account(accounts, "older")["id"] == "older"
assert tool.select_account([], None) is None
assert tool.select_account(accounts, "not-there") is None

response = SimpleNamespace(status_code=200, json=lambda: {
    "status": "unavailable", "vm_id": "DO_NOT_PRINT_VM",
    "error": "DO_NOT_PRINT_ERROR", "arbitrary_secret": "DO_NOT_PRINT_UNKNOWN"})
with patch.object(tool.requests, "get", return_value=response) as get:
    http = tool.http_probe(accounts[1]["cookies"])
assert http["http"] == 200 and http["session"]["status"] == "unavailable"
assert get.call_count == 1
assert get.call_args.args[0] == "https://muse.ai/api/session"
assert get.call_args.kwargs["allow_redirects"] is False
assert "Cookie" in get.call_args.kwargs["headers"]
assert get.call_args.kwargs["headers"]["Cookie"].endswith("x" * 5000)
assert "cookies" not in get.call_args.kwargs, "must not use a length-limited cookie jar"

with patch.object(tool.requests, "get", side_effect=tool.requests.ConnectionError("DO_NOT_PRINT_PROXY")):
    failure = tool.http_probe(accounts[1]["cookies"])
assert failure == {"error": "ConnectionError"}

response.json = lambda: (_ for _ in ()).throw(ValueError("DO_NOT_PRINT_BODY"))
with patch.object(tool.requests, "get", return_value=response):
    assert tool.http_probe({})["session"] == {"json_type": "non_json"}

class Page:
    def __init__(self, *, fail=False):
        self.fail = fail
        self.calls = []
        self.ws = SimpleNamespace(settimeout=lambda value: None)
        self.timeout = 180
    def send(self, method, params, **_kwargs):
        self.calls.append((method, params))
    def js(self, _expression, **_kwargs):
        if self.fail:
            raise RuntimeError("DO_NOT_PRINT_BROWSER_ERROR")
        return {"ready": True, "body": json.dumps({
            "status": "assigned", "vm_state": "RUNNING", "vm_id": "DO_NOT_PRINT_VM"}),
            "http": 200}

class Browser:
    instances = []
    fail = False
    def __init__(self, cfg):
        self.cfg = cfg
        self.page = None
        self.stopped = False
        self._log = None
        self.proc = SimpleNamespace(wait=lambda timeout: None)
        self.close_commands = []
        self.events = []
        self.browser = SimpleNamespace(
            send=lambda method, **kw: self.events.append(method),
            ws=SimpleNamespace(settimeout=lambda value: None), timeout=180)
        Browser.instances.append(self)
    def start(self):
        pass
    def _open_page(self):
        return Page(fail=self.fail)
    def _apply_cookies(self, page, cookies, expires):
        self.applied = (copy.deepcopy(cookies), copy.deepcopy(expires))
    def stop(self):
        self.events.append("stop")
        self.stopped = True
    def _close_page(self):
        self.events.append("close_page")

with tempfile.TemporaryDirectory() as base:
    cfg = Config(base_dir=base, profile_root=base, cdp_port=19210)
    cfg.ensure_dirs()
    Path(cfg.accounts_file).write_text(json.dumps(accounts), encoding="utf-8")
    before = Path(cfg.accounts_file).read_text(encoding="utf-8")
    with patch.object(tool, "MuseEngine", Browser):
        browser = tool.browser_probe(accounts[1], cfg)
        assert browser["session"]["status"] == "assigned"
        instance = Browser.instances[-1]
        assert instance.cfg.cdp_port != cfg.cdp_port
        assert instance.cfg.base_dir != cfg.base_dir
        assert instance.stopped and instance.applied[0] == accounts[1]["cookies"]
        assert instance.events == ["close_page", "Browser.close", "stop"], "dispose targets before shutdown"
        assert instance.page.calls == [("Page.navigate", {"url": "https://muse.ai/api/session"})]
        assert not Path(instance.cfg.base_dir).exists(), "temporary profile must be removed"
        Browser.fail = True
        failed = tool.browser_probe(accounts[1], cfg)
        assert failed["error"] == "RuntimeError" and Browser.instances[-1].stopped

    stream = io.StringIO()
    with redirect_stdout(stream), patch.object(tool, "http_probe", return_value=http), \
            patch.object(tool, "browser_probe", return_value=browser):
        assert tool.run(cfg) == 0
    printed = stream.getvalue()
    report = json.loads(printed)
    assert report["account_id"] == "newest" and report["conclusion"] == "browser_only"
    assert Path(cfg.accounts_file).read_text(encoding="utf-8") == before

assert accounts == original
for output in (http, failure, browser, failed, printed):
    assert "DO_NOT_PRINT" not in str(output), "diagnostic leaked sensitive data"
assert tool.conclusion(http, {"session": {"status": "unavailable"}}) == "both_unavailable"
assert tool.conclusion({"session": {"status": "assigned"}}, browser) == "both_assigned"
assert tool.conclusion({"session": {"status": "assigned"}}, failed) == "http_only"
assert tool.conclusion(failure, failed) == "inconclusive"
print("PASS: existing account reused; no secrets, writes, wake or generation; isolated browser cleaned")

# The supported runtime is the Linux image. Exercise real Chromium against a
# local JSON fixture there, without contacting Muse or using any real account.
if sys.platform == "linux" and os.environ.get("MUSE2API_CHROMIUM"):
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            body = b'{"status":"assigned","vm_state":"RUNNING","vm_id":"DO_NOT_PRINT_VM"}'
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        def log_message(self, *_args):
            pass
    server = HTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        url = f"http://127.0.0.1:{server.server_port}/api/session"
        with patch.object(tool, "SESSION_URL", url):
            result = tool.browser_probe({"cookies": {}}, Config())
        assert result["http"] == 200 and result["session"]["status"] == "assigned", result
        assert "DO_NOT_PRINT" not in json.dumps(result)
        print("PASS: real Linux Chromium navigation, JSON parsing and temporary profile cleanup")
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)
