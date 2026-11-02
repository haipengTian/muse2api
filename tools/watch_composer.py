#!/usr/bin/env python3
"""Observe the service's live composer; never navigate, click, type or read cookies.

Run inside the container, then click Generate once in the admin UI. Existing
pages close after each request, so attach while the request is running.
"""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import re
import sys
import time

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
    ):
        value = raw.get(key)
        result[key] = value if value in choices else 'unknown'
    for key in ('textarea', 'visible_textarea', 'editable', 'dialogs', 'frames'):
        value = raw.get(key)
        result[key] = value if type(value) is int and 0 <= value <= 10000 else -1
    for key in ('placeholder', 'connecting', 'login'):
        value = raw.get(key)
        result[key] = value if type(value) is bool else None
    return result


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
    args = parser.parse_args()
    try:
        watch(seconds=args.seconds)
    except KeyboardInterrupt:
        pass
    except Exception:
        print(json.dumps({'event': 'watch-failed'}), flush=True)
        raise SystemExit(2) from None
