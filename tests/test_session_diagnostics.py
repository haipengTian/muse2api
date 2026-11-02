"""Offline session diagnostics: preserve behavior and never disclose upstream data."""
import io
import json
import logging
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from engine import MuseAuthError, MuseEngine, MuseGenerationError


def probe(body, *, code=200, invalid_json=False):
    stream = io.StringIO()
    handler = logging.StreamHandler(stream)
    logger = logging.getLogger("muse2api")
    logger.addHandler(handler)
    reply = SimpleNamespace(status_code=code, cookies=[])
    reply.json = lambda: body
    if invalid_json:
        def decode():
            raise ValueError("DO_NOT_LOG_RESPONSE_BODY")
        reply.json = decode
    try:
        with patch("requests.get", return_value=reply) as request:
            try:
                result = MuseEngine.renew_session_http(
                    {"hatch_sess": "DO_NOT_LOG_COOKIE", "hatch_vml": "DO_NOT_LOG_COOKIE"},
                    wake_vm=False,
                )
                error = None
            except (MuseAuthError, MuseGenerationError) as exc:
                result, error = None, exc
        assert request.call_count == 1, "diagnostics must not retry session requests"
        assert request.call_args.kwargs["allow_redirects"] is False
        return result, error, stream.getvalue()
    finally:
        logger.removeHandler(handler)


result, error, logged = probe({
    "status": "unassigned", "vm_state": "SUSPENDED",
    "vm_id": "DO_NOT_LOG_VM_ID", "endpoint_url": "DO_NOT_LOG_ENDPOINT",
    "token": "DO_NOT_LOG_TOKEN", "DO_NOT_LOG_UNKNOWN_KEY": "secret",
})
assert result is None and isinstance(error, MuseGenerationError)
assert "session_probe" in logged, "missing safe session diagnostics"
assert '"status": "unassigned"' in logged
assert '"vm_state": "SUSPENDED"' in logged
assert '"json_type": "object"' in logged
assert "未返回 assigned" in str(error)

for body, expected_type in [
    ([], "array"), (None, "null"), ("DO_NOT_LOG_STRING", "string"),
    ({"status": "DO_NOT_LOG_STATUS", "vm_state": "DO_NOT_LOG_STATE"}, "object"),
    ({"status": {"secret": "DO_NOT_LOG_NESTED"}}, "object"),
    ({"status": "assigned\nDO_NOT_LOG_INJECTION"}, "object"),
]:
    _, error, output = probe(body)
    assert isinstance(error, MuseGenerationError)
    assert json.loads(output.split("http=200 ", 1)[1])["json_type"] == expected_type
    logged += output + str(error)

_, error, output = probe(None, invalid_json=True)
assert "非 JSON" in str(error) and "session_probe" in output
logged += output + str(error)
assert "DO_NOT_LOG" not in logged, "diagnostics exposed untrusted or credential data"

for code, error_type in [(401, MuseAuthError), (403, MuseGenerationError), (503, MuseGenerationError)]:
    _, error, _ = probe({}, code=code)
    assert isinstance(error, error_type) and f"HTTP {code}" in str(error)

result, error, logged = probe({"status": "assigned", "vm_state": "RUNNING"})
assert result["ok"] is True and error is None and logged == ""
print("PASS: safe session diagnostics; success/auth semantics and single request preserved")
