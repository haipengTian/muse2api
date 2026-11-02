"""Custom images update by replacing the image, never by overwriting running code."""
import asyncio
import os
import sys
import tempfile
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

with tempfile.TemporaryDirectory() as home:
    os.environ.update(MUSE2API_HOME=home, MUSE2API_PROFILE_ROOT=home, MUSE2API_KEY="test-only")
    import app

    with patch.dict(os.environ, {"MUSE2API_IMAGE_VERSION": "1.6.4-custom.1"}), \
            patch.object(app, "_ensure_git_repo", side_effect=AssertionError("must not touch git")), \
            patch("requests.get", side_effect=AssertionError("must not fetch upstream")):
        try:
            app._upgrade_from_github_sync()
        except app.HTTPException as exc:
            assert exc.status_code == 409
            assert "镜像" in exc.detail
        else:
            raise AssertionError("image mode allowed in-place code replacement")

    # Existing source installs still invoke their original upgrade mechanism.
    with patch.dict(os.environ, {"MUSE2API_IMAGE_VERSION": ""}), \
            patch.object(app, "_upgrade_from_github_sync", return_value={"ok": True}) as upgrade:
        result = asyncio.run(app.admin_upgrade_now({"restart": False}))
        assert result["ok"] is True and upgrade.call_count == 1

print("PASS: fixed custom image refuses in-place updates; source update entry point preserved")
