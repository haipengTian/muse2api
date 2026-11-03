#!/usr/bin/env python3
"""Observe the service's live composer; never navigate, click, type or read cookies.

Run inside the container, then click Generate once in the admin UI. Existing
pages close after each request, so attach while the request is running.
"""
from __future__ import annotations

import argparse
from dataclasses import replace
import json
import os
from pathlib import Path
import re
import socket
import sys
import tempfile
import time
from urllib.parse import urlsplit

ROOT = os.environ.get('MUSE2API_HOME') or (
    str(Path(__file__).resolve().parents[1]) if __file__ != '<stdin>' else os.getcwd())
sys.path.insert(0, ROOT)

from cdp import CDP, http_json
from config import CFG

STATE_JS = """(() => {
    const visible = e => e.getClientRects().length > 0
        && getComputedStyle(e).visibility !== 'hidden';
    const textareas = [...document.querySelectorAll('textarea')];
    const body = document.body ? document.body.innerText : '';
    const site = ['muse.ai', 'www.muse.ai'].includes(location.hostname) ? 'muse'
        : ['about:', 'chrome:'].includes(location.protocol) ? 'browser' : 'other';
    return {
        site,
        route: location.pathname === '/thread/new' ? 'new-thread'
            : location.pathname.startsWith('/thread/') ? 'thread' : 'other',
        ready: document.readyState,
        textarea: textareas.length,
        visible_textarea: textareas.filter(visible).length,
        editable: document.querySelectorAll('[contenteditable=true]').length,
        placeholder: !!document.querySelector('[data-testid="hatch-composer-placeholder-overlay"]'),
        dialogs: document.querySelectorAll('[role=dialog]').length,
        frames: document.querySelectorAll('iframe').length,
        connecting: body.includes('Connecting...'),
        login: /log in|sign in|create an account|登录|use another account/i.test(body),
        active: document.activeElement ? document.activeElement.tagName : 'OTHER'
        ,hydration: (() => {
            const h = document.querySelector('[data-hatch-shell-hydration-state]');
            return h ? h.getAttribute('data-hatch-shell-hydration-state') : 'missing';
        })()
        ,application_error: /application error|something went wrong|client.side exception|发生错误|出了点问题/i.test(body)
        ,body_empty: !body.trim()
    };
})()"""


def sanitize(raw):
    """Allowlist both field names and values; no page strings reach stdout."""
    raw = raw if isinstance(raw, dict) else {}
    result = {}
    for key, choices in (
        ('site', ('muse', 'browser', 'other')),
        ('route', ('new-thread', 'thread', 'other')),
        ('ready', ('loading', 'interactive', 'complete')),
        ('active', ('TEXTAREA', 'INPUT', 'DIV', 'BODY', 'IFRAME', 'OTHER')),
        ('hydration', ('missing', 'server', 'hydrated', 'hydrating')),
    ):
        value = raw.get(key)
        result[key] = value if value in choices else 'unknown'
    for key in ('textarea', 'visible_textarea', 'editable', 'dialogs', 'frames'):
        value = raw.get(key)
        result[key] = value if type(value) is int and 0 <= value <= 10000 else -1
    for key in ('placeholder', 'connecting', 'login', 'application_error', 'body_empty'):
        value = raw.get(key)
        result[key] = value if type(value) is bool else None
    return result


def error_summary(details):
    """Classify runtime exceptions without exposing messages, stacks or values."""
    exception = details.get('exception') or {}
    name = exception.get('className')
    if name not in ('TypeError', 'ReferenceError', 'SyntaxError', 'RangeError', 'Error'):
        name = 'other'
    text = str(exception.get('description') or details.get('text') or '').lower()
    kind = 'other'
    for pattern, category in (
        (r'cannot read propert|cannot convert undefined|null is not an object', 'undefined-access'),
        (r'is not a function', 'not-function'),
        (r'is not defined', 'undefined-name'),
        (r'hydrat', 'hydration'),
        (r'failed to fetch|networkerror|network error', 'network'),
        (r'chunkloaderror|loading chunk', 'asset-load'),
    ):
        if re.search(pattern, text):
            kind = category
            break
    return {'class': name, 'kind': kind}


class EventSocket:
    """Observe events consumed by the diagnostic's own CDP client."""
    def __init__(self, sock, emit):
        self.sock = sock
        self.emit = emit
        self.sessions = []

    def __getattr__(self, name):
        return getattr(self.sock, name)

    def recv(self):
        raw = self.sock.recv()
        try:
            message = json.loads(raw)
            params = message.get('params') or {}
            if message.get('method') == 'Runtime.exceptionThrown':
                self.emit('runtime-error', error=error_summary(params.get('exceptionDetails') or {}))
            elif message.get('method') == 'Runtime.consoleAPICalled' and params.get('type') == 'error':
                # Console arguments may contain credentials or conversation text.
                self.emit('console-error')
            elif message.get('method') == 'Network.responseReceived':
                response = params.get('response') or {}
                url = urlsplit(response.get('url') or '')
                if url.hostname in ('muse.ai', 'www.muse.ai') and url.path == '/api/session':
                    status = response.get('status')
                    if type(status) in (int, float) and 100 <= status <= 599:
                        self.emit('session-response', http=int(status))
                    self.sessions.append(params.get('requestId'))
        except Exception:
            pass
        return raw


def prepare_probe(engine, account, emit, sample):
    """Observe initialization first; if it survives, reproduce pre-input UI steps."""
    engine._last_http_renew[account['id']] = time.time()  # No HTTP renew or VM wake.
    engine.ensure_page(account['cookies'], account.get('cookies_exp'), account_id=account['id'])
    emit('stage', name='passive-after-ready')
    for _ in range(50):
        if sample().get('textarea') == 0:
            emit('stage', name='input-missing-before-ui-actions')
            return
        time.sleep(.1)
    for name, action in (
        ('reset-thread', lambda: engine.reset_thread(for_chat=False)),
        ('scroll-bottom', engine._scroll_bottom),
        ('clear-attachments', engine._clear_attachments),
        ('read-attachments', engine.attachments),
        ('read-agent-count', engine._agent_count),
        ('focus-input', lambda: engine.page.js("""(() => {
            const ta = document.querySelector('textarea');
            if (ta) { ta.scrollIntoView({block:'center', inline:'nearest'}); ta.focus(); }
        })()""")),
    ):
        emit('stage', name=name)
        action()
        if sample().get('textarea') == 0:
            emit('stage', name='input-missing-after-' + name)
            return
    for _ in range(30):
        sample()
        time.sleep(.1)
    emit('stage', name='stopped-before-typing')


def probe(cfg=CFG):
    """Use a saved account in an owned temporary browser, without generating."""
    from engine import MuseEngine, _session_probe_summary
    from store import Store
    from tools.diagnose_session import select_account

    account = select_account(Store(cfg).accounts)
    if account is None:
        print(json.dumps({'event': 'account-not-found'}), flush=True)
        return
    start = time.monotonic()

    def emit(event, **data):
        print(json.dumps({'event': event, 'seconds': round(time.monotonic() - start, 2),
                          **data}, ensure_ascii=False), flush=True)

    with tempfile.TemporaryDirectory(prefix='muse-composer-probe-', dir=cfg.home_dir,
                                     ignore_cleanup_errors=sys.platform == 'win32') as tmp:
        with socket.socket() as sock:
            sock.bind(('127.0.0.1', 0))
            port = sock.getsockname()[1]
        isolated = replace(cfg, base_dir=tmp, profile_root=os.path.join(tmp, 'profiles'), cdp_port=port)
        isolated.ensure_dirs()
        engine = MuseEngine(isolated)
        engine._ensure_local().proxy_snapshot = account.get('proxy')
        previous = None

        def collect_sessions(page):
            if not isinstance(page.ws, EventSocket):
                return
            for request_id in page.ws.sessions[:5]:
                try:
                    result = page.send('Network.getResponseBody', {'requestId': request_id}, timeout=2)
                    body = result.get('result', {}).get('body', '')
                    summary = json.loads(_session_probe_summary(json.loads(body)))
                except Exception:
                    continue  # Response may not be complete yet; try the next sample.
                page.ws.sessions.remove(request_id)
                emit('session', summary=summary)

        def sample():
            nonlocal previous
            state = sanitize(engine.page.js(STATE_JS, timeout=2))
            if state != previous:
                previous = state
                emit('state', state=state)
            return state

        try:
            engine.start()
            original_open = engine._open_page

            def open_observed(*args, **kwargs):
                page = original_open(*args, **kwargs)
                page.timeout = 3
                page.ws.settimeout(.5)
                page.ws = EventSocket(page.ws, emit)
                original_js = page.js

                def observed_js(expression, *args, **kwargs):
                    # Sample after each real engine JS call, never modify its result.
                    nonlocal previous
                    result = original_js(expression, *args, **kwargs)
                    state = sanitize(result if expression == STATE_JS
                                     else original_js(STATE_JS, timeout=2))
                    if state != previous:
                        previous = state
                        emit('state', state=state)
                    collect_sessions(page)
                    return result

                page.js = observed_js
                return page

            engine._open_page = open_observed
            emit('stage', name='load-without-ui-actions')
            prepare_probe(engine, account, emit, sample)
        except Exception:
            # Neither engine exceptions nor page exceptions are safe free text.
            emit('preparation-failed')
            if engine.page:
                try:
                    sample()
                except Exception:
                    pass
        finally:
            if engine.page:
                collect_sessions(engine.page)
            engine._close_page()
            if engine.proc is not None and engine.browser is not None:
                try:
                    engine.browser.send('Browser.close', timeout=3)
                    engine.proc.wait(timeout=5)
                except Exception:
                    pass
            engine.stop()
            if engine._log:
                engine._log.close()
            emit('done')


def watch(cfg=CFG, seconds=90):
    start = time.monotonic()
    connections = {}
    next_number = 1
    discovery_failed = False

    def emit(event, **data):
        print(json.dumps({'event': event, 'seconds': round(time.monotonic() - start, 2),
                          **data}, ensure_ascii=False), flush=True)

    version = os.getenv('MUSE2API_IMAGE_VERSION', '')
    if not re.fullmatch(r'\d+\.\d+\.\d+-custom\.\d+', version):
        version = 'unknown'
    emit('watching', version=version)
    print('监测已启动，现在到后台点一次“生成图片”；输出只含页面状态。',
          file=sys.stderr, flush=True)
    try:
        while time.monotonic() - start < seconds:
            try:
                targets = http_json(f'http://127.0.0.1:{cfg.cdp_port}/json', timeout=.8)
                if not isinstance(targets, list):
                    raise ValueError('invalid target list')
                discovery_failed = False
            except Exception:
                if not discovery_failed:
                    emit('browser-unavailable')
                discovery_failed = True
                time.sleep(.2)
                continue
            live = {p.get('id'): p for p in targets if p.get('type') == 'page' and p.get('id')}
            for target in list(connections):
                if target not in live:
                    entry = connections.pop(target)
                    entry['page'].close()
                    emit('page-closed', page=entry['number'])
            for target, info in live.items():
                if target not in connections:
                    try:
                        page = CDP(info['webSocketDebuggerUrl'], timeout=.8)
                        page.ws.settimeout(.4)
                    except Exception:
                        continue
                    connections[target] = {'page': page, 'number': next_number, 'last': None}
                    next_number += 1
                entry = connections[target]
                try:
                    state = sanitize(entry['page'].js(STATE_JS, timeout=.8))
                    if state != entry['last']:
                        entry['last'] = state
                        emit('state', page=entry['number'], state=state)
                except Exception:
                    if entry['last'] != 'unavailable':
                        entry['last'] = 'unavailable'
                        emit('page-unavailable', page=entry['number'])
            time.sleep(.1)
    finally:
        for entry in connections.values():
            entry['page'].close()
        emit('done')


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--seconds', type=int, default=90, choices=range(10, 301),
                        metavar='10-300')
    parser.add_argument('--probe', action='store_true',
                        help='Automatically reproduce preparation with a saved account; never type or send')
    args = parser.parse_args()
    try:
        if args.probe:
            probe()
        else:
            watch(seconds=args.seconds)
    except KeyboardInterrupt:
        pass
    except Exception:
        print(json.dumps({'event': 'watch-failed'}), flush=True)
        raise SystemExit(2) from None
