#!/usr/bin/env python3
"""Compare HTTP and isolated Chromium sessions using an existing local account.

Run inside Muse2API 1.6.4-custom.2 or newer. No account writes or generation.
"""
from __future__ import annotations

import argparse
from contextlib import nullcontext
from dataclasses import replace
import json
import os
from pathlib import Path
import socket
import sys
import tempfile
import time

# Also support `docker exec -i muse2api python -` without installing this file.
ROOT = os.environ.get("MUSE2API_HOME") or (
    str(Path(__file__).resolve().parents[1]) if __file__ != "<stdin>" else os.getcwd())
sys.path.insert(0, ROOT)

import requests
from config import CFG
from engine import MuseEngine, _session_probe_summary
from store import Store

SESSION_URL = "https://muse.ai/api/session"
DOCUMENT_JS = """((url) => {
  const expected = new URL(url);
  if (document.readyState !== 'complete' || location.href === 'about:blank')
    return {ready: false};
  if (location.origin !== expected.origin || location.pathname !== expected.pathname)
    return {ready: true, navigation: 'not_session'};
  const body = document.body ? document.body.innerText : '';
  if (body.length > 262144) return {ready: true, navigation: 'body_too_large'};
  const nav = performance.getEntriesByType('navigation')[0];
  return {ready: true, body, http: nav ? nav.responseStatus : null};
})(%s)"""


def select_account(accounts, account_id=None):
    if account_id:
        return next((a for a in accounts if a.get("id") == account_id and a.get("cookies")), None)
    enabled = [a for a in accounts if a.get("enabled", True) and a.get("cookies")]
    return max(enabled, key=lambda a: a.get("created_at") or 0, default=None)


def summarize(body):
    return json.loads(_session_probe_summary(body))


def http_probe(cookies, proxy=None):
    # Match the service request, including a raw Cookie header (no curl jar).
    headers = {
        "User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/135.0.0.0 Safari/537.36",
        "Origin": "https://muse.ai",
        "Referer": "https://muse.ai/thread/new",
        "Sec-Fetch-Site": "same-origin", "Sec-Fetch-Mode": "cors", "Sec-Fetch-Dest": "empty",
        "Cookie": "; ".join(f"{k}={v}" for k, v in cookies.items() if v),
    }
    try:
        if proxy:
            from proxy import request_proxy_context
            routing = request_proxy_context(proxy)
        else:
            routing = nullcontext({})
        with routing as options:
            response = requests.get(SESSION_URL, headers=headers, timeout=12, allow_redirects=False,
                                    **options)
        try:
            session = summarize(response.json())
        except ValueError:
            session = {"json_type": "non_json"}
        return {"http": response.status_code, "session": session}
    except requests.RequestException as exc:
        return {"error": type(exc).__name__}


def browser_probe(account, cfg):
    # Own a temporary profile, port and process; never attach to the service CDP.
    with tempfile.TemporaryDirectory(prefix="muse-session-diagnose-", dir=cfg.home_dir) as tmp:
        with socket.socket() as sock:
            sock.bind(("127.0.0.1", 0))
            port = sock.getsockname()[1]
        isolated = replace(cfg, base_dir=tmp, profile_root=os.path.join(tmp, "profiles"), cdp_port=port)
        isolated.ensure_dirs()
        browser = MuseEngine(isolated)
        try:
            browser.start()
            browser.browser.timeout = 5
            browser.browser.ws.settimeout(5)
            page = browser._open_page(proxy=account['proxy']) if account.get('proxy') else browser._open_page()
            page.timeout = 5
            page.ws.settimeout(5)
            browser.page = page
            browser._apply_cookies(page, account["cookies"], account.get("cookies_exp"))
            page.send("Page.navigate", {"url": SESSION_URL}, timeout=20)
            deadline = time.monotonic() + 20
            while time.monotonic() < deadline:
                doc = page.js(DOCUMENT_JS % json.dumps(SESSION_URL), timeout=5)
                if isinstance(doc, dict) and doc.get("ready") is True:
                    navigation = doc.get("navigation")
                    if navigation in ("not_session", "body_too_large"):
                        return {"navigation": navigation}
                    try:
                        session = summarize(json.loads(doc.get("body", "")))
                    except (ValueError, TypeError):
                        session = {"json_type": "non_json"}
                    result = {"session": session}
                    code = doc.get("http")
                    if type(code) is int and 100 <= code <= 599:
                        result["http"] = code
                    return result
                time.sleep(0.25)
            return {"error": "DocumentTimeout"}
        except Exception as exc:
            # Exception strings can contain proxy credentials or response data.
            return {"error": type(exc).__name__}
        finally:
            # Dispose the live context before shutting down its CDP connection.
            browser._close_page()
            if browser.proc is not None and browser.browser is not None:
                try:
                    browser.browser.send("Browser.close", timeout=3)
                    browser.proc.wait(timeout=5)
                except Exception:
                    pass  # stop() still terminates the owned process if needed.
            browser.stop()
            if browser._log is not None:
                browser._log.close()


def conclusion(http, browser):
    hs = http.get("session", {}).get("status")
    bs = browser.get("session", {}).get("status")
    if hs == bs == "assigned":
        return "both_assigned"
    if bs == "assigned":
        return "browser_only"
    if hs == "assigned":
        return "http_only"
    if hs == bs == "unavailable":
        return "both_unavailable"
    return "inconclusive"


def run(cfg=CFG, account_id=None):
    account = select_account(Store(cfg).accounts, account_id)
    if account is None:
        print(json.dumps({"error": "account_not_found"}))
        return 2
    print("正在检查 HTTP 和独立浏览器会话，通常需几十秒……", file=sys.stderr, flush=True)
    http = http_probe(account["cookies"], **({'proxy': account['proxy']} if account.get('proxy') else {}))
    browser = browser_probe(account, cfg)
    report = {
        "account_id": account["id"],
        "proxy_env_present": any(os.environ.get(k) for k in (
            "HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY", "http_proxy", "https_proxy", "all_proxy")),
        "http": http, "browser": browser,
        "conclusion": conclusion(http, browser),
    }
    print(json.dumps(report, ensure_ascii=False, indent=2), flush=True)
    return 0


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--account", help="Account ID; default: latest enabled import")
    args = parser.parse_args()
    try:
        raise SystemExit(run(account_id=args.account))
    except Exception as exc:
        print(json.dumps({"error": type(exc).__name__}))
        raise SystemExit(2) from None
