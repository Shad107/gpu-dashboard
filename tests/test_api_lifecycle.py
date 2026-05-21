"""Tests for /api/restart and /api/stop — both run the side-effect in a
separate thread, so we just verify the immediate response shape + that the
handler is non-blocking.

We don't actually let the threads execute sys.exit / os.execv during tests
(both would kill pytest). The fake ctx has no sampler/storage to stop, and
we mock os.execv / sys.exit to assert they were scheduled.
"""
from __future__ import annotations

import sys
import time

import pytest

from gpu_dashboard import api


class TestHandleStop:
    def test_returns_ok_immediately(self, monkeypatch):
        # Prevent sys.exit from killing the test runner
        called = {"exit": False}
        monkeypatch.setattr(sys, "exit", lambda code=0: called.__setitem__("exit", True))

        ctx = {}
        code, body = api.handle_stop(ctx)
        assert code == 200
        assert body["ok"] is True
        assert body["message"] == "stopping"

        # Give the daemon thread time to run
        time.sleep(1.0)
        assert called["exit"] is True

    def test_stops_sampler_and_storage(self, monkeypatch):
        monkeypatch.setattr(sys, "exit", lambda code=0: None)

        stopped = {"sampler": False, "retention": False, "storage": False}
        class FakeSampler:
            def stop(self): stopped["sampler"] = True
        class FakeRetention:
            def stop(self): stopped["retention"] = True
        class FakeStorage:
            def close(self): stopped["storage"] = True

        ctx = {"sampler": FakeSampler(), "retention": FakeRetention(), "storage": FakeStorage()}
        api.handle_stop(ctx)
        time.sleep(1.0)
        assert all(stopped.values()), f"not all components stopped: {stopped}"


class TestHandleRestart:
    def test_returns_ok_immediately(self, monkeypatch):
        called = {"execv": False}
        import os as _os
        monkeypatch.setattr(_os, "execv", lambda path, args: called.__setitem__("execv", True))

        ctx = {}
        code, body = api.handle_restart(ctx)
        assert code == 200
        assert body["ok"] is True
        assert body["message"] == "restarting"

        time.sleep(1.0)
        assert called["execv"] is True
