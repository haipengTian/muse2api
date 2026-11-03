"""Personal diagnostic entry points must use the configured account exit."""
from contextlib import nullcontext, redirect_stdout
from dataclasses import replace
import importlib.util
import io
import json
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
def load(name):
    spec = importlib.util.spec_from_file_location(name, ROOT / 'tools' / (name + '.py'))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module

diagnostic = load('diagnose_session')
watcher = load('watch_composer')
from config import Config
import proxy

PROXY = {'server': 'http://proxy.example:8080', 'password': 'PRIVATE_DIAGNOSTIC_PROXY'}
reply = SimpleNamespace(status_code=200, json=lambda: {'status': 'assigned'})
options = {'proxies': {'https': 'http://relay:1'}}
with patch.object(proxy, 'request_proxy_context', return_value=nullcontext(options)) as route, \
        patch.object(diagnostic.requests, 'get', return_value=reply) as get:
    report = diagnostic.http_probe({'hatch_sess': 'PRIVATE_COOKIE'}, proxy=PROXY)
    route.assert_called_once_with(PROXY)
    assert get.call_args.kwargs['proxies'] == options['proxies']
    assert report['session']['status'] == 'assigned'

class Browser:
    instances = []
    def __init__(self, cfg):
        self.cfg = cfg
        self.local = SimpleNamespace()
        self.page = None
        self.proc = None
        self._log = None
        self._last_http_renew = {}
        self.browser = SimpleNamespace(ws=SimpleNamespace(settimeout=lambda _: None), send=lambda *a, **kw: {})
        Browser.instances.append(self)
    def start(self): pass
    def _ensure_local(self): return self.local
    def _open_page(self, **kwargs):
        self.open_options = kwargs
        return SimpleNamespace(ws=SimpleNamespace(settimeout=lambda _: None),
            send=lambda *a, **kw: {}, js=lambda *a, **kw: {
                'ready': True, 'body': '{"status":"assigned"}', 'http': 200})
    def _apply_cookies(self, *a): pass
    def _close_page(self): self.page = None
    def stop(self): pass

with tempfile.TemporaryDirectory() as home:
    cfg = Config(base_dir=home, profile_root=home)
    cfg.ensure_dirs()
    account = {'id': 'fixture', 'cookies': {'hatch_sess': 'PRIVATE_COOKIE'}, 'proxy': PROXY}
    Path(cfg.accounts_file).write_text(json.dumps([account]), encoding='utf-8')
    with patch.object(diagnostic, 'MuseEngine', Browser):
        diagnostic.browser_probe(account, cfg)
    assert Browser.instances[-1].open_options == {'proxy': PROXY}
    import engine
    def prepare(browser, selected, emit, sample):
        assert browser._ensure_local().proxy_snapshot == PROXY
        emit('test-preparation')
    output = io.StringIO()
    with patch.object(engine, 'MuseEngine', Browser), \
            patch.object(watcher, 'prepare_probe', side_effect=prepare), redirect_stdout(output):
        watcher.probe(cfg)
    assert 'test-preparation' in output.getvalue()
    assert 'PRIVATE' not in output.getvalue()
print('PASS: HTTP, browser and preparation diagnostics keep account proxy without leaking credentials')
