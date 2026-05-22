"""R&D #6.8 — cgroup per-process GPU power accounting tests."""
import subprocess
from unittest.mock import patch, mock_open
from gpu_dashboard import api
from gpu_dashboard.config import Config


def _ctx():
    return {"config": Config(defaults={})}


class FakeProc:
    def __init__(self, stdout="", returncode=0):
        self.stdout = stdout
        self.returncode = returncode


def test_normalize_cgroup_v2_format():
    assert api._normalize_cgroup("/system.slice/llama-server.service") == "system.slice/llama-server.service"


def test_normalize_cgroup_collapses_user_slice_noise():
    p = "/user.slice/user-1000.slice/user@1000.service/app.slice/firefox.service"
    assert api._normalize_cgroup(p) == "user.slice/firefox.service"


def test_normalize_cgroup_root():
    assert api._normalize_cgroup("/") == "root"
    assert api._normalize_cgroup("") == "root"


def test_no_nvidia_smi_returns_unavailable():
    with patch.object(subprocess, "run", side_effect=FileNotFoundError):
        code, body = api.handle_cgroup_power(_ctx())
    assert code == 200
    assert body["available"] is False


def test_aggregates_by_cgroup_active_workload():
    """Two processes : llama-server using 80% SM, Xorg 5% — split power 150W."""
    fake_power = FakeProc(stdout="150.0\n")
    fake_pmon = FakeProc(stdout="\n".join([
        "# gpu pid type sm mem enc dec jpg ofa fb ccpm command",
        "# Idx # C/G % % % % % % MB MB name",
        "    0     1785     C   80    0    0    0    0    0  23584      0    llama-server",
        "    0   165012     G    5    0    0    0    0    0      9      0    Xorg",
    ]))
    calls = [fake_power, fake_pmon]
    def run_mock(cmd, **kw):
        return calls.pop(0)
    fake_cgroup_data = {
        1785: "0::/system.slice/llama-server.service",
        165012: "0::/system.slice/display-manager.service",
    }
    def open_mock(path, *a, **kw):
        for pid, content in fake_cgroup_data.items():
            if path == f"/proc/{pid}/cgroup":
                return mock_open(read_data=content + "\n").return_value
        raise OSError(f"no mock for {path}")
    with patch.object(subprocess, "run", side_effect=run_mock), \
         patch("builtins.open", side_effect=open_mock):
        code, body = api.handle_cgroup_power(_ctx())
    assert code == 200
    assert body["available"] is True
    assert body["total_power_w"] == 150.0
    assert body["total_sm_pct"] == 85.0
    assert len(body["cgroups"]) == 2
    # llama-server : SM share 80/85 → 0.941 * 150 = 141.18 W
    llama = next(c for c in body["cgroups"] if "llama" in c["name"])
    assert 140 < llama["est_watts"] < 142
    assert llama["pids"] == [1785]
    # Xorg : 5/85 → 0.0588 * 150 = 8.82 W
    xorg = next(c for c in body["cgroups"] if "display" in c["name"])
    assert 7 < xorg["est_watts"] < 10
    # Sorted by est_watts desc
    assert body["cgroups"][0]["est_watts"] > body["cgroups"][1]["est_watts"]


def test_idle_all_sm_zero_falls_back_to_vram_share():
    """All SM% = 0 (idle GPU). Attribution falls back to VRAM share."""
    fake_power = FakeProc(stdout="10.0\n")
    fake_pmon = FakeProc(stdout="\n".join([
        "# header",
        "# header2",
        "    0     1785     C   -    -    -    -    -    -  20000      0    llama-server",
        "    0     1900     C   -    -    -    -    -    -   4000      0    other",
    ]))
    calls = [fake_power, fake_pmon]
    def run_mock(cmd, **kw):
        return calls.pop(0)
    def open_mock(path, *a, **kw):
        if path == "/proc/1785/cgroup":
            return mock_open(read_data="0::/system.slice/llama.service\n").return_value
        if path == "/proc/1900/cgroup":
            return mock_open(read_data="0::/user.slice/other.service\n").return_value
        raise OSError(f"no mock for {path}")
    with patch.object(subprocess, "run", side_effect=run_mock), \
         patch("builtins.open", side_effect=open_mock):
        code, body = api.handle_cgroup_power(_ctx())
    assert code == 200
    assert body["total_sm_pct"] == 0.0
    # 20000 / 24000 = 0.833 * 10W = 8.33 W (llama-server holds most VRAM)
    llama = next(c for c in body["cgroups"] if "llama" in c["name"])
    assert 8 < llama["est_watts"] < 9


def test_unknown_cgroup_falls_back_to_pid_label():
    """If /proc/<pid>/cgroup is unreadable, name is 'pid:N'."""
    fake_power = FakeProc(stdout="50.0\n")
    fake_pmon = FakeProc(stdout="\n".join([
        "# header",
        "# header2",
        "    0     9999     C   30    0    0    0    0    0   1000      0    mystery",
    ]))
    calls = [fake_power, fake_pmon]
    def run_mock(cmd, **kw):
        return calls.pop(0)
    with patch.object(subprocess, "run", side_effect=run_mock), \
         patch("builtins.open", side_effect=OSError("no /proc")):
        code, body = api.handle_cgroup_power(_ctx())
    assert code == 200
    assert len(body["cgroups"]) == 1
    assert body["cgroups"][0]["name"] == "pid:9999"


def test_empty_pmon_returns_zero_cgroups():
    fake_power = FakeProc(stdout="5.0\n")
    fake_pmon = FakeProc(stdout="# header\n# header2\n")  # no processes
    calls = [fake_power, fake_pmon]
    def run_mock(cmd, **kw):
        return calls.pop(0)
    with patch.object(subprocess, "run", side_effect=run_mock):
        code, body = api.handle_cgroup_power(_ctx())
    assert code == 200
    assert body["available"] is True
    assert body["cgroups"] == []
    assert body["total_sm_pct"] == 0.0
