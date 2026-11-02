"""The live watcher must follow page creation/removal without leaking page data."""
import importlib.util
import io
import json
import os
import socket
import tempfile
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
