"""R&D #6.3 — System-context sidecar tests."""
from unittest.mock import patch, mock_open
from gpu_dashboard import api
from gpu_dashboard.config import Config


def _ctx():
    return {"config": Config(defaults={})}


def _reset_caches():
    api._LAST_CPU_LINE = None
    api._LAST_CPU_TS = 0.0
    api._LAST_VMSTAT = {}
    api._LAST_VMSTAT_TS = 0.0


def test_first_call_returns_no_cpu_baseline():
    """First call : no previous /proc/stat → cpu_pct + iowait_pct = null."""
    _reset_caches()
    fake_stat = "cpu  100 0 50 1000 20 0 0 0 0 0\n"
    fake_load = "0.5 0.6 0.7 1/100 12345\n"
    fake_vm = "pswpin 0\npswpout 0\npgmajfault 0\n"
    fake_mem = "MemTotal:  16000000 kB\nMemAvailable:  8000000 kB\nSwapTotal: 0 kB\nSwapFree: 0 kB\n"
    sources = {"/proc/stat": fake_stat, "/proc/loadavg": fake_load,
               "/proc/vmstat": fake_vm, "/proc/meminfo": fake_mem}
    def open_side_effect(path, *a, **kw):
        return mock_open(read_data=sources[path]).return_value
    with patch("builtins.open", side_effect=open_side_effect):
        code, body = api.handle_sys_context(_ctx())
    assert code == 200
    assert body["available"] is True
    assert body["cpu_pct"] is None  # no baseline
    assert body["iowait_pct"] is None
    assert body["loadavg_1"] == 0.5
    assert body["mem_used_pct"] == 50.0  # (16M - 8M) / 16M = 50%


def test_second_call_computes_cpu_delta():
    """After 2 calls, cpu_pct + iowait_pct populated from the delta."""
    _reset_caches()
    # Call 1 : 100 user, 1000 idle, 20 iowait → total cpu = 1170
    fake_stat_1 = "cpu  100 0 50 1000 20 0 0 0 0 0\n"
    # Call 2 : delta = 200 user, 0 nice, 50 system, 800 idle, 50 iowait
    fake_stat_2 = "cpu  300 0 100 1800 70 0 0 0 0 0\n"
    fake_load = "0.5 0.6 0.7 1/100 12345\n"
    fake_vm = "pswpin 0\npswpout 0\npgmajfault 0\n"
    fake_mem = "MemTotal:  16000000 kB\nMemAvailable:  8000000 kB\nSwapTotal: 0 kB\nSwapFree: 0 kB\n"

    # Call 1
    sources1 = {"/proc/stat": fake_stat_1, "/proc/loadavg": fake_load,
                "/proc/vmstat": fake_vm, "/proc/meminfo": fake_mem}
    with patch("builtins.open",
               side_effect=lambda p, *a, **kw: mock_open(read_data=sources1[p]).return_value):
        api.handle_sys_context(_ctx())

    # Call 2
    sources2 = {**sources1, "/proc/stat": fake_stat_2}
    with patch("builtins.open",
               side_effect=lambda p, *a, **kw: mock_open(read_data=sources2[p]).return_value):
        code, body = api.handle_sys_context(_ctx())
    assert body["cpu_pct"] is not None
    # delta total = 350, idle delta = 800, iowait delta = 50
    # cpu_pct = 100*(350-800)/350 ... wait diff total is sum of all : 200+0+50+800+50 = 1100
    # cpu% = 100*(1100-800)/1100 ≈ 27.3
    assert 25 < body["cpu_pct"] < 30
    # iowait% = 100*50/1100 ≈ 4.5
    assert 4.0 < body["iowait_pct"] < 5.5


def test_swap_used_pct_computed():
    _reset_caches()
    fake_stat = "cpu  100 0 50 1000 20 0 0 0 0 0\n"
    fake_load = "1.0 1.0 1.0 1/100 12345\n"
    fake_vm = "pswpin 0\npgmajfault 0\n"
    fake_mem = "MemTotal: 16000000 kB\nMemAvailable: 8000000 kB\nSwapTotal: 4000000 kB\nSwapFree: 1000000 kB\n"
    sources = {"/proc/stat": fake_stat, "/proc/loadavg": fake_load,
               "/proc/vmstat": fake_vm, "/proc/meminfo": fake_mem}
    with patch("builtins.open",
               side_effect=lambda p, *a, **kw: mock_open(read_data=sources[p]).return_value):
        code, body = api.handle_sys_context(_ctx())
    # swap_used_pct = 100 * (4M - 1M) / 4M = 75%
    assert body["swap_used_pct"] == 75.0
    assert body["swap_total_kb"] == 4_000_000


def test_swap_total_zero_returns_none_pct():
    """No swap configured → swap_used_pct is None (not 0 or divide-by-zero)."""
    _reset_caches()
    fake_mem = "MemTotal: 16000000 kB\nMemAvailable: 8000000 kB\nSwapTotal: 0 kB\nSwapFree: 0 kB\n"
    sources = {
        "/proc/stat": "cpu 1 0 1 1 0 0 0 0 0 0\n",
        "/proc/loadavg": "1.0 1.0 1.0 1/100 12345\n",
        "/proc/vmstat": "pswpin 0\npgmajfault 0\n",
        "/proc/meminfo": fake_mem,
    }
    with patch("builtins.open",
               side_effect=lambda p, *a, **kw: mock_open(read_data=sources[p]).return_value):
        _, body = api.handle_sys_context(_ctx())
    assert body["swap_used_pct"] is None
    assert body["swap_total_kb"] == 0


def test_proc_stat_unreadable_returns_null_cpu():
    """If /proc/stat is unreadable, we still return other fields."""
    _reset_caches()
    def fail_open(path, *a, **kw):
        if path == "/proc/stat":
            raise OSError("denied")
        if path == "/proc/loadavg":
            return mock_open(read_data="1.0 2.0 3.0 1/100 12345\n").return_value
        if path == "/proc/vmstat":
            return mock_open(read_data="pswpin 0\npgmajfault 0\n").return_value
        if path == "/proc/meminfo":
            return mock_open(read_data="MemTotal: 16000000 kB\nMemAvailable: 8000000 kB\n").return_value
        raise OSError(f"unexpected {path}")
    with patch("builtins.open", side_effect=fail_open):
        code, body = api.handle_sys_context(_ctx())
    assert body["loadavg_1"] == 1.0
    assert body["loadavg_5"] == 2.0
    assert body["loadavg_15"] == 3.0
    assert body["cpu_pct"] is None
