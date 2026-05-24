"""Tests for modules/proc_status_caps_audit.py — R&D #80.4."""
from __future__ import annotations

import pytest

from gpu_dashboard.modules import proc_status_caps_audit as mod


STATUS_TEMPLATE = """Name:\t{name}
PPid:\t{ppid}
Uid:\t{uid_r}\t{uid_e}\t{uid_s}\t{uid_f}
CapInh:\t{cap_inh:016x}
CapPrm:\t{cap_prm:016x}
CapEff:\t{cap_eff:016x}
CapBnd:\t{cap_bnd:016x}
CapAmb:\t{cap_amb:016x}
NoNewPrivs:\t{nnp}
Seccomp:\t{seccomp}
"""


def _mk_status(tmp_path, pid, *, name="testproc", ppid=1,
                uid_e=1000, cap_eff=0, cap_amb=0,
                nnp=1, seccomp=2):
    d = tmp_path / str(pid)
    d.mkdir(parents=True, exist_ok=True)
    (d / "status").write_text(STATUS_TEMPLATE.format(
        name=name, ppid=ppid, uid_r=uid_e, uid_e=uid_e,
        uid_s=uid_e, uid_f=uid_e,
        cap_inh=0, cap_prm=0, cap_eff=cap_eff,
        cap_bnd=0x1ffffffffff, cap_amb=cap_amb,
        nnp=nnp, seccomp=seccomp))


# --- parse_status ----------------------------------------------

def test_parse_uid_eff():
    text = STATUS_TEMPLATE.format(
        name="x", ppid=1, uid_r=1000, uid_e=1000,
        uid_s=1000, uid_f=1000, cap_inh=0, cap_prm=0,
        cap_eff=0, cap_bnd=0, cap_amb=0, nnp=1, seccomp=2)
    out = mod.parse_status(text)
    assert out["uid_eff"] == 1000
    assert out["name"] == "x"
    assert out["ppid"] == 1


def test_parse_cap_eff():
    text = STATUS_TEMPLATE.format(
        name="x", ppid=1, uid_r=0, uid_e=0,
        uid_s=0, uid_f=0, cap_inh=0, cap_prm=0,
        cap_eff=(1 << 21), cap_bnd=0, cap_amb=0,
        nnp=1, seccomp=2)
    out = mod.parse_status(text)
    assert out["cap_eff"] == (1 << 21)  # CAP_SYS_ADMIN


# --- _dangerous_caps -------------------------------------------

def test_dangerous_caps_none():
    assert mod._dangerous_caps(0) == []


def test_dangerous_caps_sys_admin():
    bits = 1 << mod.CAP_SYS_ADMIN
    assert mod._dangerous_caps(bits) == ["CAP_SYS_ADMIN"]


def test_dangerous_caps_multiple():
    bits = (1 << mod.CAP_SYS_ADMIN) | (1 << mod.CAP_NET_ADMIN)
    out = mod._dangerous_caps(bits)
    assert "CAP_SYS_ADMIN" in out
    assert "CAP_NET_ADMIN" in out


def test_dangerous_caps_skips_safe_bit():
    # CAP_NET_BIND_SERVICE = 10, not in dangerous list
    bits = 1 << 10
    assert mod._dangerous_caps(bits) == []


# --- scan_pid --------------------------------------------------

def test_scan_pid_unreadable(tmp_path):
    assert mod.scan_pid(str(tmp_path), 99999) is None


def test_scan_pid_ok(tmp_path):
    _mk_status(tmp_path, 100, name="bash")
    out = mod.scan_pid(str(tmp_path), 100)
    assert out["pid"] == 100
    assert out["uid_eff"] == 1000


# --- classify --------------------------------------------------

def test_classify_unknown_no_pids():
    v = mod.classify([], 0)
    assert v["verdict"] == "unknown"


def test_classify_requires_root():
    v = mod.classify([], 366)
    assert v["verdict"] == "requires_root"


def _info(pid=1, name="x", ppid=1, uid_eff=0,
          cap_eff=0, cap_amb=0, nnp=1):
    return {"pid": pid, "name": name, "ppid": ppid,
              "uid_real": uid_eff, "uid_eff": uid_eff,
              "cap_inh": 0, "cap_prm": 0,
              "cap_eff": cap_eff, "cap_bnd": 0,
              "cap_amb": cap_amb,
              "no_new_privs": nnp, "seccomp": 2}


def test_classify_ok():
    v = mod.classify(
        [_info(uid_eff=0, cap_eff=0),
         _info(pid=2, uid_eff=1000, cap_eff=0)], 2)
    assert v["verdict"] == "ok"


def test_classify_root_with_caps_ok():
    # root with CAP_SYS_ADMIN — that's normal
    v = mod.classify(
        [_info(uid_eff=0, cap_eff=(1 << mod.CAP_SYS_ADMIN))],
        1)
    assert v["verdict"] == "ok"


def test_classify_unexpected_full_caps_userland():
    # ollama daemon spawned by docker/podman (not systemd) —
    # has CAP_SYS_ADMIN as a non-root user → real risk
    v = mod.classify(
        [_info(pid=42, name="ollama", ppid=99,
                 uid_eff=1000,
                 cap_eff=(1 << mod.CAP_SYS_ADMIN))], 1)
    assert v["verdict"] == "unexpected_full_caps_userland"
    assert v["pid"] == 42
    assert "CAP_SYS_ADMIN" in v["caps"]


def test_classify_systemd_hardened_service_ok():
    # systemd-networkd: uid 998 with CAP_NET_ADMIN, PPid=1 —
    # this is deliberate systemd-hardening, not a risk.
    v = mod.classify(
        [_info(pid=1539, name="systemd-network",
                 ppid=1, uid_eff=998,
                 cap_eff=(1 << mod.CAP_NET_ADMIN))], 1)
    assert v["verdict"] == "ok"


def test_classify_chrome_sandbox_ok():
    # Chrome / VSCode legitimately hold CAP_SYS_ADMIN
    # for namespace-based sandbox, regardless of PPid.
    v = mod.classify(
        [_info(pid=3670, name="code", ppid=3639,
                 uid_eff=1000,
                 cap_eff=(1 << mod.CAP_SYS_ADMIN))], 1)
    assert v["verdict"] == "ok"


def test_classify_unexpected_dac_override():
    # non-systemd-spawned process with CAP_DAC_OVERRIDE
    v = mod.classify(
        [_info(uid_eff=1000, ppid=42,
                 cap_eff=(1 << mod.CAP_DAC_OVERRIDE))], 1)
    assert v["verdict"] == "unexpected_full_caps_userland"


def test_classify_userland_safe_cap_ok():
    # uid 1000 with CAP_NET_BIND_SERVICE (bit 10) — not flagged
    v = mod.classify(
        [_info(uid_eff=1000, cap_eff=(1 << 10))], 1)
    assert v["verdict"] == "ok"


def test_classify_ambient_caps_set():
    # CAP_NET_ADMIN (12) ambient outside systemd → flagged
    v = mod.classify(
        [_info(pid=100, ppid=99,
                 cap_amb=(1 << mod.CAP_NET_ADMIN))], 1)
    assert v["verdict"] == "ambient_caps_set_outside_systemd"


def test_classify_ambient_caps_direct_systemd_child_ok():
    v = mod.classify(
        [_info(pid=100, ppid=1,
                 cap_amb=(1 << mod.CAP_NET_ADMIN))], 1)
    assert v["verdict"] == "ok"


def test_classify_ambient_benign_cap_ok():
    # sddm-helper holds CAP_AUDIT_READ (bit 35) as ambient —
    # not a dangerous cap, should not trigger.
    v = mod.classify(
        [_info(pid=2351, name="sddm-helper", ppid=1972,
                 cap_amb=(1 << 35))], 1)
    assert v["verdict"] == "ok"


def test_classify_safe_cap_ok():
    # CAP_NET_BIND_SERVICE (bit 10) on non-root non-systemd
    # child — not dangerous enough to flag.
    v = mod.classify(
        [_info(uid_eff=1000, ppid=99,
                 cap_eff=(1 << 10), nnp=0)], 1)
    assert v["verdict"] == "ok"


# Priority : unexpected > ambient > nnp_off
def test_priority_unexpected_over_ambient():
    v = mod.classify(
        [_info(pid=1, ppid=99, cap_amb=1),
         _info(pid=2, ppid=42, uid_eff=1000,
                  cap_eff=(1 << mod.CAP_SYS_ADMIN))], 2)
    assert v["verdict"] == "unexpected_full_caps_userland"


# --- status integration ----------------------------------------

def test_status_unknown(tmp_path):
    out = mod.status(None, str(tmp_path / "nope_proc"),
                       str(tmp_path / "nope_cap"))
    assert out["ok"] is False
    assert out["verdict"]["verdict"] == "unknown"


def test_status_ok_synthetic(tmp_path):
    _mk_status(tmp_path, 100, name="bash", uid_e=1000,
                cap_eff=0)
    _mk_status(tmp_path, 200, name="root", uid_e=0,
                cap_eff=(1 << mod.CAP_SYS_ADMIN))
    cap_last = tmp_path / "cap_last_cap"
    cap_last.write_text("40\n")
    out = mod.status(None, str(tmp_path), str(cap_last))
    assert out["ok"] is True
    assert out["pid_count_total"] == 2
    assert out["cap_last_cap"] == 40
    assert out["verdict"]["verdict"] == "ok"


def test_status_unexpected_full_caps(tmp_path):
    # ppid != 1 → not a systemd daemon → flagged
    _mk_status(tmp_path, 42, name="ollama", ppid=99,
                uid_e=1000,
                cap_eff=(1 << mod.CAP_SYS_ADMIN))
    out = mod.status(None, str(tmp_path),
                       str(tmp_path / "nope_cap"))
    assert out["ok"] is False
    assert (out["verdict"]["verdict"]
            == "unexpected_full_caps_userland")
