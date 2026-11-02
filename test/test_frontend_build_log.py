"""The update-driven frontend build reports what it did and keeps its output.

``build_frontend_sync`` / ``build_frontend_async`` are what the three
source-install update paths call before they reinstall and restart. Each must
tell a failed build (``static/dist`` still serves the previous bundle) from a
skipped one, and leave the npm output where the failure can be read afterwards.
"""

from __future__ import annotations

import asyncio
import os
import stat
import sys
from pathlib import Path

import pytest

from kiro_crew import frontend
from kiro_crew.config.paths import config_dir


@pytest.fixture
def website(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    site = tmp_path / "proj" / "website"
    site.mkdir(parents=True)
    (site / "package.json").write_text("{}")
    monkeypatch.delenv("KIROCREW_EDITION_DIR", raising=False)
    monkeypatch.setattr(frontend.shutil, "which", lambda _name: "/usr/bin/npm")
    monkeypatch.setattr(frontend, "_stage_dist_locked", lambda *_a, **_k: True)
    return site


def _write_output(kwargs: dict, text: str) -> None:
    """Write *text* the way a child would: straight to the descriptor it was given."""
    out = kwargs.get("stdout")
    assert hasattr(out, "fileno"), f"output is not a file: {out!r}"
    os.write(out.fileno(), text.encode())


def _popen(website: Path, *, build_rc: int):
    """A Popen double for the install (``npm ci``/``install``) and the build."""

    class _P:
        pid = 4242

        def __init__(self, argv, **kwargs) -> None:
            self.argv = list(argv)
            self.kwargs = kwargs
            self.returncode = None

        def communicate(self, timeout=None):
            _write_output(self.kwargs, "added 812 packages\n")
            self.returncode = 0
            return (None, None)

        def wait(self, timeout=None):
            _write_output(self.kwargs, "vite v8 building for production...\n")
            if build_rc == 0:
                (website / "dist").mkdir(exist_ok=True)
                (website / "dist" / "index.html").write_text("<html></html>")
            else:
                _write_output(
                    self.kwargs,
                    "error TS2304: Cannot find name 'Foo'\n"
                    "fetch https://user:hunter2pass@registry.example.com/x\n",
                )
            self.returncode = build_rc
            return build_rc

    return _P


def _expected_log() -> Path:
    return config_dir() / "logs" / "frontend-build.log"


def test_a_failed_sync_build_reports_failed_and_keeps_its_output(website, monkeypatch):
    monkeypatch.setattr(frontend.subprocess, "Popen", _popen(website, build_rc=1))

    result = frontend.build_frontend_sync(website.parent, log=lambda _m: None)

    assert result.outcome == "failed"
    assert result.log_path == _expected_log()
    text = result.log_path.read_text()
    assert "$ npm install" in text and "added 812 packages" in text
    assert "$ npm run build" in text and "error TS2304" in text
    message = result.failure_message()
    assert str(result.log_path) in message
    assert "error TS2304: Cannot find name 'Foo'" in message
    # The tail is shown in progress cards and the console, so it is redacted.
    assert "hunter2pass" not in message
    if sys.platform != "win32":
        assert stat.S_IMODE(result.log_path.stat().st_mode) == 0o600


def test_a_sync_build_that_publishes_reports_built(website, monkeypatch):
    monkeypatch.setattr(frontend.subprocess, "Popen", _popen(website, build_rc=0))

    result = frontend.build_frontend_sync(website.parent, log=lambda _m: None)

    assert result.outcome == "built"
    assert result.log_path == _expected_log()


def test_a_sync_build_that_cannot_stage_reports_failed(website, monkeypatch):
    monkeypatch.setattr(frontend.subprocess, "Popen", _popen(website, build_rc=0))
    monkeypatch.setattr(frontend, "_stage_dist_locked", lambda *_a, **_k: False)

    result = frontend.build_frontend_sync(website.parent, log=lambda _m: None)

    assert result.outcome == "failed"


def test_a_failed_sync_install_reports_failed(website, monkeypatch):
    popen = _popen(website, build_rc=0)

    def _failing_install(self, timeout=None):
        _write_output(self.kwargs, "npm ERR! code E401\n")
        self.returncode = 1
        return (None, None)

    monkeypatch.setattr(popen, "communicate", _failing_install)
    monkeypatch.setattr(frontend.subprocess, "Popen", popen)

    result = frontend.build_frontend_sync(website.parent, log=lambda _m: None)

    assert result.outcome == "failed"
    assert "npm ERR! code E401" in result.tail


def test_no_website_dir_is_skipped_not_failed(tmp_path):
    result = frontend.build_frontend_sync(tmp_path, log=lambda _m: None)

    assert result.outcome == "skipped"


def test_no_npm_is_skipped_not_failed(website, monkeypatch):
    monkeypatch.setattr(frontend.shutil, "which", lambda _name: None)

    result = frontend.build_frontend_sync(website.parent, log=lambda _m: None)

    assert result.outcome == "skipped"


def _async_install(rc: int = 0):
    class _Proc:
        returncode = None

        async def wait(self):
            self.returncode = rc
            return rc

    async def _exec(*_argv, **kwargs):
        _write_output(kwargs, "added 812 packages\n")
        return _Proc()

    return _exec


def test_a_failed_async_build_reports_failed_and_keeps_its_output(website, monkeypatch):
    monkeypatch.setattr(frontend.asyncio, "create_subprocess_exec", _async_install())
    monkeypatch.setattr(frontend.subprocess, "Popen", _popen(website, build_rc=1))
    notes: list[tuple[str, str]] = []

    result = asyncio.run(
        frontend.build_frontend_async(
            str(website.parent), push_progress=lambda s, m: notes.append((s, m))
        )
    )

    assert result.outcome == "failed"
    assert result.log_path == _expected_log()
    text = result.log_path.read_text()
    assert "added 812 packages" in text and "error TS2304" in text
    assert str(result.log_path) in result.failure_message()
    # The existing per-cause warning is still pushed.
    assert any("Frontend build failed" in m for _s, m in notes), notes


def test_an_async_build_that_publishes_reports_built(website, monkeypatch):
    monkeypatch.setattr(frontend.asyncio, "create_subprocess_exec", _async_install())
    monkeypatch.setattr(frontend.subprocess, "Popen", _popen(website, build_rc=0))

    result = asyncio.run(frontend.build_frontend_async(str(website.parent)))

    assert result.outcome == "built"


def test_a_failed_async_install_reports_failed(website, monkeypatch):
    monkeypatch.setattr(frontend.asyncio, "create_subprocess_exec", _async_install(rc=1))
    monkeypatch.setattr(frontend.subprocess, "Popen", _popen(website, build_rc=0))

    result = asyncio.run(frontend.build_frontend_async(str(website.parent)))

    assert result.outcome == "failed"
    assert "added 812 packages" in result.tail


def test_async_no_npm_is_skipped_not_failed(website, monkeypatch):
    monkeypatch.setattr(frontend.shutil, "which", lambda _name: None)

    result = asyncio.run(frontend.build_frontend_async(str(website.parent)))

    assert result.outcome == "skipped"


def test_an_unopenable_log_still_builds_with_output_discarded(website, monkeypatch):
    monkeypatch.setattr(frontend, "_open_build_log", lambda: (None, None))
    seen: list[object] = []
    popen = _popen(website, build_rc=1)

    def _wait(self, timeout=None):
        seen.append(self.kwargs.get("stdout"))
        self.returncode = 1
        return 1

    def _communicate(self, timeout=None):
        self.returncode = 0
        return (b"", b"")

    monkeypatch.setattr(popen, "wait", _wait)
    monkeypatch.setattr(popen, "communicate", _communicate)
    monkeypatch.setattr(frontend.subprocess, "Popen", popen)

    result = frontend.build_frontend_sync(website.parent, log=lambda _m: None)

    assert result.outcome == "failed"
    assert result.log_path is None
    assert seen == [frontend.subprocess.DEVNULL]
    assert "Build output" not in result.failure_message()
