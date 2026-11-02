"""Regression: preparing a fresh composer must finish before sending a prompt."""
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import engine as module


class Page:
    def __init__(self, navigate=True, fail=False, navigation_error=False, fresh=True):
        self.navigate = navigate
        self.fail = fail
        self.navigations = []
        self.navigation_error = navigation_error
        self.fresh = fresh
    def js(self, expr):
        if self.fail:
            raise RuntimeError("fixture navigation failure")
        if "function(forChat)" in expr:
            return self.navigate
        if "return 'ready'" in expr:
            return "ready"
        if "window.location.pathname" in expr:
            return self.fresh
        return True
    def send(self, method, params):
        self.navigations.append((method, params))
        return {"result": {"errorText": "DO_NOT_PRINT_NAVIGATION"}} if self.navigation_error else {"result": {}}


with tempfile.TemporaryDirectory() as profile:
    engine = module.MuseEngine(SimpleNamespace(profile_dir=profile, login_wait=45))
    engine.page = Page()
    with patch.object(engine, "_wait_ws_ready", return_value=False) as ready, \
            patch.object(module.time, "sleep"):
        try:
            engine.reset_thread()
        except module.MuseGenerationError:
            pass
        else:
            raise AssertionError("reset ignored composer readiness failure and allowed prompt sending")
        assert ready.call_args.kwargs["timeout"] == 45

    engine.page = Page(navigate=False)
    with patch.object(engine, "_wait_ws_ready", return_value=True) as ready:
        engine.reset_thread(for_chat=True)
        assert engine.page.navigations == [], "reusable warm chat must not navigate"
        assert ready.call_count == 1, "warm composer must also be confirmed ready"

    engine.page = Page(fail=True)
    try:
        engine.reset_thread()
    except module.MuseGenerationError as exc:
        assert "fixture navigation failure" not in str(exc), "do not expose CDP exception contents"
    else:
        raise AssertionError("reset swallowed navigation failure")

    engine.page = Page()
    with patch.object(engine, "_wait_ws_ready", return_value=True), \
            patch.object(module.time, "sleep"):
        engine.reset_thread()
        assert engine.page.navigations == [
            ("Page.navigate", {"url": "https://muse.ai/thread/new"})]

    engine.page = None
    engine.reset_thread()  # Existing cleanup callers may have already closed it.

    engine.page = Page(navigation_error=True)
    with patch.object(engine, "_wait_ws_ready", return_value=True):
        try:
            engine.reset_thread()
        except module.MuseGenerationError as exc:
            assert "DO_NOT_PRINT_NAVIGATION" not in str(exc)
        else:
            raise AssertionError("navigation error allowed sending into a ready historical thread")

    class Clock:
        value = 0
        def time(self):
            self.value += 1
            return self.value
        def sleep(self, _seconds):
            pass
    engine.page = Page(fresh=False)
    with patch.object(module, "time", Clock()):
        try:
            engine.reset_thread()
        except module.MuseGenerationError:
            pass
        else:
            raise AssertionError("old thread remained ready and was mistaken for a new empty thread")

print("PASS: reset confirms composer readiness; failures stop before send; warm chats remain reusable")
