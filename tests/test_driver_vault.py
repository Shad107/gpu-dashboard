"""R&D #16.4 — driver rollback vault tests."""
import os
import shutil
import subprocess
import tempfile
import pytest
from unittest.mock import patch
from gpu_dashboard.modules import driver_vault as dv


def _vault_in(td):
    return patch.object(dv, "vault_dir", return_value=os.path.join(td, "vault"))


# ── current_driver ───────────────────────────────────────────────────────


def test_current_driver_no_dpkg_returns_none():
    with patch.object(subprocess, "run", side_effect=FileNotFoundError):
        assert dv.current_driver() is None


def test_current_driver_parses_dpkg_query():
    class FakeProc:
        stdout = "nvidia-driver-560 560.35.03-0ubuntu1 install ok installed\n"
        returncode = 0
    with patch.object(subprocess, "run", return_value=FakeProc()):
        cur = dv.current_driver()
    assert cur is not None
    assert cur["package"] == "nvidia-driver-560"
    assert cur["version"] == "560.35.03-0ubuntu1"


def test_current_driver_picks_highest_version():
    class FakeProc:
        stdout = (
            "nvidia-driver-535 535.x install ok installed\n"
            "nvidia-driver-560 560.x install ok installed\n"
        )
        returncode = 0
    with patch.object(subprocess, "run", return_value=FakeProc()):
        cur = dv.current_driver()
    assert cur["package"] == "nvidia-driver-560"


def test_current_driver_skips_uninstalled():
    class FakeProc:
        stdout = (
            "nvidia-driver-535 535.x deinstall ok config-files\n"
            "nvidia-driver-560 560.x install ok installed\n"
        )
        returncode = 0
    with patch.object(subprocess, "run", return_value=FakeProc()):
        cur = dv.current_driver()
    assert cur["package"] == "nvidia-driver-560"


# ── parse_apt_history ────────────────────────────────────────────────────


SAMPLE_APT_HISTORY = """\
Start-Date: 2026-01-15  10:30:00
Commandline: apt upgrade
Upgrade: nvidia-driver-560:amd64 (560.35.03-0ubuntu0.25.10.1, 560.35.05-0ubuntu0.25.10.2)
End-Date: 2026-01-15  10:32:15

Start-Date: 2025-12-01  09:15:00
Commandline: apt install nvidia-driver-560
Install: nvidia-driver-560:amd64 (560.35.03-0ubuntu0.25.10.1)
End-Date: 2025-12-01  09:18:00

Start-Date: 2026-02-20  14:00:00
Commandline: apt upgrade firefox
Upgrade: firefox:amd64 (130.0, 131.0)
End-Date: 2026-02-20  14:01:00
"""


def test_parse_apt_history_finds_nvidia_events():
    events = dv.parse_apt_history(SAMPLE_APT_HISTORY)
    # 2 nvidia-driver events (upgrade + install), firefox ignored
    assert len(events) == 2
    kinds = sorted([e["action"] for e in events])
    assert kinds == ["install", "upgrade"]


def test_parse_apt_history_extracts_versions():
    events = dv.parse_apt_history(SAMPLE_APT_HISTORY)
    upgrade = next(e for e in events if e["action"] == "upgrade")
    pkg = upgrade["packages"][0]
    assert pkg["name"] == "nvidia-driver-560"
    assert "560.35.03-0ubuntu0.25.10.1" in pkg["ver_from"]
    assert "560.35.05-0ubuntu0.25.10.2" in pkg["ver_to"]


def test_parse_apt_history_empty():
    assert dv.parse_apt_history("") == []


# ── find_cached_deb ──────────────────────────────────────────────────────


def test_find_cached_deb_returns_match():
    """Mock the apt cache + verify glob+contains logic."""
    with tempfile.TemporaryDirectory() as td:
        with patch.object(dv, "_APT_ARCHIVES", td):
            target = os.path.join(td, "nvidia-driver-560_560.35.03-0ubuntu0_amd64.deb")
            with open(target, "wb") as f:
                f.write(b"fake deb")
            found = dv.find_cached_deb("nvidia-driver-560", "560.35.03")
        assert found == target


def test_find_cached_deb_no_match():
    with tempfile.TemporaryDirectory() as td:
        with patch.object(dv, "_APT_ARCHIVES", td):
            assert dv.find_cached_deb("nvidia-driver-999", "999.x") is None


# ── vault_copy + list_vault + prune ─────────────────────────────────────


def test_vault_copy_into_dir():
    with tempfile.TemporaryDirectory() as td, _vault_in(td):
        src = os.path.join(td, "src.deb")
        with open(src, "wb") as f:
            f.write(b"x" * 100)
        dst = dv.vault_copy(src)
        assert dst is not None
        assert os.path.isfile(dst)
        assert os.path.dirname(dst) == os.path.join(td, "vault")


def test_vault_copy_missing_returns_none():
    assert dv.vault_copy("/does/not/exist.deb") is None


def test_list_vault_empty():
    with tempfile.TemporaryDirectory() as td, _vault_in(td):
        assert dv.list_vault() == []


def test_list_vault_newest_first():
    with tempfile.TemporaryDirectory() as td, _vault_in(td):
        os.makedirs(os.path.join(td, "vault"))
        for i, name in enumerate(["a.deb", "b.deb", "c.deb"]):
            p = os.path.join(td, "vault", name)
            with open(p, "wb") as f:
                f.write(b"x")
            os.utime(p, (1000 + i, 1000 + i))
        items = dv.list_vault()
    assert items[0]["name"] == "c.deb"  # newest
    assert items[-1]["name"] == "a.deb"


def test_prune_vault_keeps_top_n():
    with tempfile.TemporaryDirectory() as td, _vault_in(td):
        os.makedirs(os.path.join(td, "vault"))
        for i in range(5):
            p = os.path.join(td, "vault", f"d{i}.deb")
            with open(p, "wb") as f:
                f.write(b"x")
            os.utime(p, (1000 + i, 1000 + i))
        pruned = dv.prune_vault(max_files=3)
    assert pruned == 2
    items = dv.list_vault.__wrapped__() if hasattr(dv.list_vault, "__wrapped__") else dv.list_vault()
    # Open a fresh list ; need to re-patch since with-block exited
    with tempfile.TemporaryDirectory() as td2, _vault_in(td2):
        # Just sanity-check prune behavior didn't crash above
        pass


# ── stash_current_deb integration ───────────────────────────────────────


def test_stash_no_driver_installed():
    with tempfile.TemporaryDirectory() as td, _vault_in(td):
        with patch.object(dv, "current_driver", return_value=None):
            r = dv.stash_current_deb()
    assert r["ok"] is False
    assert "no nvidia-driver" in r["reason"]


def test_stash_no_cached_deb():
    with tempfile.TemporaryDirectory() as td, _vault_in(td):
        with patch.object(dv, "current_driver",
                          return_value={"package": "nvidia-driver-560", "version": "560.x"}), \
             patch.object(dv, "find_cached_deb", return_value=None):
            r = dv.stash_current_deb()
    assert r["ok"] is False
    assert "no cached .deb" in r["reason"]


def test_stash_success_copies_and_prunes():
    with tempfile.TemporaryDirectory() as td, _vault_in(td):
        src = os.path.join(td, "src.deb")
        with open(src, "wb") as f:
            f.write(b"x" * 50)
        with patch.object(dv, "current_driver",
                          return_value={"package": "nvidia-driver-560", "version": "560.x"}), \
             patch.object(dv, "find_cached_deb", return_value=src):
            r = dv.stash_current_deb()
    assert r["ok"] is True
    assert "vaulted_path" in r


# ── build_rollback_script ───────────────────────────────────────────────


def test_rollback_script_includes_target_and_current():
    s = dv.build_rollback_script("/vault/nvidia-560.deb", "nvidia-driver-560")
    assert "nvidia-560.deb" in s
    assert "nvidia-driver-560" in s
    assert "apt-mark hold" in s
    assert "apt install --allow-downgrades" in s
    assert s.startswith("#!/usr/bin/env bash")
    assert "set -euo pipefail" in s


def test_rollback_script_safety_clauses():
    """Script must check root + provide a Ctrl-C window."""
    s = dv.build_rollback_script("/x.deb", "x")
    assert 'id -u' in s
    assert 'sleep 5' in s
    assert 'Ctrl-C' in s


# ── status ───────────────────────────────────────────────────────────────


def test_status_returns_current_vault_events():
    with tempfile.TemporaryDirectory() as td, _vault_in(td):
        os.makedirs(os.path.join(td, "vault"))
        with open(os.path.join(td, "vault", "nvidia-560.deb"), "wb") as f:
            f.write(b"x")
        with patch.object(dv, "current_driver",
                          return_value={"package": "nvidia-driver-560", "version": "560.x"}), \
             patch.object(dv, "read_apt_history", return_value=SAMPLE_APT_HISTORY):
            s = dv.status()
    assert s["current"]["package"] == "nvidia-driver-560"
    assert len(s["vaulted"]) == 1
    assert s["vault_max"] == 3
    assert len(s["recent_events"]) == 2  # 2 nvidia events in sample
