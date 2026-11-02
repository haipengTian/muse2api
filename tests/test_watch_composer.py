"""The live watcher must follow page creation/removal without leaking page data."""
import importlib.util
import io
import json
import os
import socket
import tempfile
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from contextlib import redirect_stdout
from pathlib import Path
import sys
from types import SimpleNamespace
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
path = ROOT / 'tools/watch_composer.py'
assert path.is_file(), 'missing live composer watcher'
spec = importlib.util.spec_from_file_location('watch_composer', path)
tool = importlib.util.module_from_spec(spec)
spec.loader.exec_module(tool)

secret = 'DO_NOT_PRINT_COOKIE_OR_CHAT'
state = {'site': 'muse', 'route': 'new-thread', 'ready': 'complete',
         'textarea': 1, 'visible_textarea': 1, 'editable': 0,
         'placeholder': True, 'dialogs': 0, 'frames': 0,
         'connecting': False, 'login': False, 'active': 'TEXTAREA'}
assert secret not in json.dumps(tool.sanitize({**state, 'body': secret, 'url': secret}))
assert secret not in json.dumps(tool.sanitize({k: secret for k in state}))

# A saved-account probe must observe passive initialization before any UI action.
assert hasattr(tool, 'probe'), 'missing automatic page preparation probe'
assert tool.error_summary({'exception': {'className': 'TypeError',
    'description': 'TypeError: Cannot read properties of undefined '+secret}}) == {
        'class': 'TypeError', 'kind': 'undefined-access'}
assert secret not in json.dumps(tool.error_summary({'text': secret,
    'exception': {'className': secret, 'description': secret}}))

class Preparation:
    def __init__(self):
        self._last_http_renew = {}
        self.calls = []
        self.page = SimpleNamespace(js=lambda expression: self.calls.append('focus-input'))
    def ensure_page(self, *args, **kwargs):
        self.calls.append('load')
    def reset_thread(self, **kwargs):
        self.calls.append('reset-thread')
    def _scroll_bottom(self):
        self.calls.append('scroll-bottom')
    def _clear_attachments(self):
        self.calls.append('clear-attachments')
    def attachments(self):
        self.calls.append('read-attachments')
    def _agent_count(self):
        self.calls.append('read-agent-count')

events = []
preparation = Preparation()
with patch.object(tool.time, 'sleep'):
    tool.prepare_probe(preparation, {'id': 'test', 'cookies': {'private': secret}},
                       lambda event, **data: events.append(data), lambda: {'textarea': 0})
assert preparation.calls == ['load'], 'passive disappearance must stop before any UI action'
assert events[-1]['name'] == 'input-missing-before-ui-actions'
preparation = Preparation()
sequence = iter([{'textarea': 1}, {'textarea': 0}, {'textarea': 1}])
with patch.object(tool.time, 'sleep'):
    tool.prepare_probe(preparation, {'id': 'test', 'cookies': {}},
                       lambda *a, **kw: None, lambda: next(sequence))
assert preparation.calls == ['load'], 'transient disappearance must also stop passive probe'
preparation = Preparation()
with patch.object(tool.time, 'sleep'):
    tool.prepare_probe(preparation, {'id': 'test', 'cookies': {}},
                       lambda *a, **kw: None, lambda: {'textarea': 1})
assert preparation.calls == ['load', 'reset-thread', 'scroll-bottom', 'clear-attachments',
                             'read-attachments', 'read-agent-count', 'focus-input']
assert 'test' in preparation._last_http_renew

class Clock:
    value = 0
    def monotonic(self):
        return self.value
    def sleep(self, seconds):
        self.value += seconds

class Page:
    instances = []
    def __init__(self, url, **kwargs):
        self.closed = False
        self.ws = SimpleNamespace(settimeout=lambda timeout: None)
        self.reads = 0
        self.calls = []
        self.instances.append(self)
    def js(self, expression, **kwargs):
        self.calls.append(expression)
        self.reads += 1
        return {**state, 'textarea': 1 if self.reads <= 2 else 0, 'body': secret}
    def close(self):
        self.closed = True

clock = Clock()
def pages(url, **kwargs):
    if clock.value < .15 or clock.value >= .7:
        return []
    return [{'id': 'private-target', 'type': 'page', 'url': 'https://muse.ai/thread/'+secret,
             'webSocketDebuggerUrl': 'ws://127.0.0.1/private-target'}]

output = io.StringIO()
with patch.object(tool, 'time', clock), patch.object(tool, 'http_json', pages), \
        patch.object(tool, 'CDP', Page), redirect_stdout(output):
    tool.watch(SimpleNamespace(cdp_port=19210), seconds=1)
reports = [json.loads(line) for line in output.getvalue().splitlines()]
snapshots = [r for r in reports if r['event'] == 'state']
assert [r['state']['textarea'] for r in snapshots] == [1, 0], snapshots
assert any(r['event'] == 'page-closed' for r in reports)
assert reports[-1]['event'] == 'done'
assert secret not in output.getvalue() and 'private-target' not in output.getvalue()
assert all(p.closed for p in Page.instances)
assert len(Page.instances) == 1, 'must reuse one observer connection per target'
assert all(expr == tool.STATE_JS for p in Page.instances for expr in p.calls)

# A CDP failure may contain secrets; report a fixed status and always detach.
output = io.StringIO()
clock = Clock()
with patch.object(tool, 'time', clock), patch.object(tool, 'http_json', pages), \
        patch.object(tool, 'CDP', Page), \
        patch.object(Page, 'js', side_effect=RuntimeError(secret)), redirect_stdout(output):
    tool.watch(SimpleNamespace(cdp_port=19210), seconds=.5)
assert secret not in output.getvalue()
assert all(p.closed for p in Page.instances)
print('PASS: observes new/disappearing composers, deduplicates, redacts and detaches')

if os.environ.get('MUSE2API_CHROMIUM'):
    from config import Config
    from engine import MuseEngine

    with tempfile.TemporaryDirectory(prefix='muse-watch-test-', dir=Path.home(),
                                     ignore_cleanup_errors=True) as tmp:
        with socket.socket() as sock:
            sock.bind(('127.0.0.1', 0))
            port = sock.getsockname()[1]
        cfg = Config(base_dir=tmp, profile_root=tmp, cdp_port=port)
        cfg.ensure_dirs()
        engine = MuseEngine(cfg)
        try:
            engine.start()
            page = engine._open_page()
            engine.page = page
            page.js("""document.body.innerHTML='<textarea></textarea><p>DO_NOT_PRINT_COOKIE_OR_CHAT</p>';
                setTimeout(() => document.querySelector('textarea').remove(), 500); true;""")
            output = io.StringIO()
            with redirect_stdout(output):
                tool.watch(cfg, seconds=1.5)
            reports = [json.loads(line) for line in output.getvalue().splitlines()]
            counts = [r['state']['textarea'] for r in reports if r['event'] == 'state']
            assert 1 in counts and 0 in counts, counts
            assert secret not in output.getvalue()
            assert page.js('document.querySelectorAll("textarea").length') == 0
            assert engine.current_context_id, 'fixture must use the service incognito path'
            print('PASS: real Chromium observes incognito textarea removal and keeps target alive')
        finally:
            engine._close_page()
            if engine.browser and engine.proc:
                try:
                    engine.browser.send('Browser.close', timeout=3)
                    engine.proc.wait(timeout=5)
                except Exception:
                    pass
            engine.stop()
            if engine._log:
                engine._log.close()

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            if self.path == '/api/session':
                body = json.dumps({'status': 'unavailable', 'vm_id': secret}).encode()
                content_type = 'application/json'
            else:
                body = b'''<html><body><textarea></textarea><script>
                  fetch('/api/session');
                  setTimeout(() => {
                    document.querySelector('textarea').remove();
                    const missing = undefined;
                    missing.value;
                  }, 500);
                </script></body></html>'''
                content_type = 'text/html'
            self.send_response(200)
            self.send_header('Content-Type', content_type)
            self.send_header('Content-Length', str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        def log_message(self, *_args):
            pass

    server = HTTPServer(('127.0.0.1', 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    original_open = MuseEngine._open_page

    def fixture_open(self):
        page = original_open(self)
        send = page.send
        def fixture_send(method, params=None, timeout=None):
            if method == 'Page.navigate' and (params or {}).get('url') == 'https://muse.ai/thread/new':
                params = {'url': f'http://127.0.0.1:{server.server_port}/thread/new'}
            return send(method, params, timeout)
        page.send = fixture_send
        return page

    try:
        with tempfile.TemporaryDirectory(prefix='muse-probe-test-', dir=Path.home(),
                                         ignore_cleanup_errors=True) as tmp:
            cfg = Config(base_dir=tmp, profile_root=tmp)
            cfg.ensure_dirs()
            account_data = json.dumps([{'id': 'fake', 'cookies': {'fake': secret}, 'enabled': True}])
            Path(cfg.accounts_file).write_text(account_data, encoding='utf-8')
            output = io.StringIO()
            with patch.object(MuseEngine, '_open_page', fixture_open), \
                    patch.object(MuseEngine, 'renew_session_http', side_effect=AssertionError('no renew')), \
                    redirect_stdout(output):
                tool.probe(cfg)
            reports = [json.loads(line) for line in output.getvalue().splitlines()]
            assert any(r.get('name') == 'input-missing-before-ui-actions' for r in reports), reports
            assert any(r.get('error', {}).get('kind') == 'undefined-access' for r in reports), reports
            assert secret not in output.getvalue()
            assert not any(r.get('name') == 'reset-thread' for r in reports)
            assert Path(cfg.accounts_file).read_text(encoding='utf-8') == account_data
            assert reports[-1]['event'] == 'done'
            print('PASS: automatic real Chromium preparation detects passive crash, no renew or account writes')
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)
