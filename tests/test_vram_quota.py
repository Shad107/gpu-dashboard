"""R&D #13.3 — VRAM quota enforcer tests."""
import json
import os
import signal
import subprocess
import tempfile
import time
import pytest
from unittest.mock import patch
from gpu_dashboard.modules import vram_quota as vq


def _with_tmp_paths(td):
    return patch.multiple(
        vq,
        rules_path=lambda: os.path.join(td, "rules.json"),
        state_path=lambda: os.path.join(td, "state.json"),
        audit_path=lambda: os.path.join(td, "audit.json"),
    )


def _rule(rid="cap", regex="llama-server", max_mib=20000, grace_s=60, action="warn"):
    return {
        "id": rid, "process_regex": regex,
        "max_vram_mib": max_mib, "grace_s": grace_s, "action": action,
    }


def _proc(pid=1785, name="/home/olivier/llama.cpp/build/bin/llama-server", used=23584):
    return {"pid": pid, "name": name, "used_memory_mib": used}


# ── validate_rule ────────────────────────────────────────────────────────


def test_valid_rule_passes():
    assert vq.validate_rule(_rule()) is None


def test_rule_missing_id_fails():
    r = _rule()
    del r["id"]
    assert "id" in vq.validate_rule(r)


def test_rule_invalid_regex_fails():
    r = _rule(regex="[unclosed")
    assert "process_regex" in vq.validate_rule(r)


def test_rule_invalid_action_fails():
    r = _rule(action="nuke")
    assert "action" in vq.validate_rule(r)


def test_rule_non_integer_max_fails():
    r = _rule(max_mib="huge")
    assert "max_vram_mib" in vq.validate_rule(r)


# ── probe_compute_apps ───────────────────────────────────────────────────


def test_probe_parses_csv_output():
    """Sample csv from nvidia-smi --query-compute-apps."""
    fake_out = "1785, /home/olivier/llama.cpp/build/bin/llama-server, 23584\n7890, ollama, 4096\n"
    class FakeProc:
        stdout = fake_out
        returncode = 0
    with patch.object(subprocess, "run", return_value=FakeProc()):
        procs = vq.probe_compute_apps()
    assert len(procs) == 2
    assert procs[0]["pid"] == 1785
    assert procs[0]["used_memory_mib"] == 23584
    assert procs[1]["pid"] == 7890


def test_probe_handles_missing_nvidia_smi():
    with patch.object(subprocess, "run", side_effect=FileNotFoundError):
        assert vq.probe_compute_apps() == []


def test_probe_handles_non_zero_exit():
    class FakeProc:
        stdout = ""
        returncode = 1
    with patch.object(subprocess, "run", return_value=FakeProc()):
        assert vq.probe_compute_apps() == []


# ── evaluate ─────────────────────────────────────────────────────────────


def test_evaluate_no_rules_returns_empty():
    with tempfile.TemporaryDirectory() as td, _with_tmp_paths(td):
        assert vq.evaluate(processes=[_proc()]) == []


def test_evaluate_under_quota_no_fire():
    with tempfile.TemporaryDirectory() as td, _with_tmp_paths(td):
        vq.save_rules([_rule(max_mib=30000)])
        assert vq.evaluate(processes=[_proc(used=20000)]) == []


def test_evaluate_over_quota_fires_warn():
    """warn action : escalation=watching until grace_s elapsed, dry_run=True implicitly."""
    with tempfile.TemporaryDirectory() as td, _with_tmp_paths(td):
        vq.save_rules([_rule(max_mib=10000, grace_s=60, action="warn")])
        fires = vq.evaluate(processes=[_proc(used=20000)], now=1000)
    assert len(fires) == 1
    assert fires[0]["pid"] == 1785
    assert fires[0]["dry_run"] is True
    assert fires[0]["escalation"] == "watching"


def test_evaluate_state_tracks_first_breach():
    """Same process breached twice — second eval shows elapsed > 0."""
    with tempfile.TemporaryDirectory() as td, _with_tmp_paths(td):
        vq.save_rules([_rule(max_mib=10000, grace_s=60, action="warn")])
        vq.evaluate(processes=[_proc(used=20000)], now=1000)
        fires = vq.evaluate(processes=[_proc(used=20000)], now=1030)
    assert fires[0]["breached_for_s"] == 30


def test_evaluate_state_resets_when_under_quota():
    """Breach then drop below : state forgotten, future eval starts fresh."""
    with tempfile.TemporaryDirectory() as td, _with_tmp_paths(td):
        vq.save_rules([_rule(max_mib=10000, grace_s=60, action="warn")])
        vq.evaluate(processes=[_proc(used=20000)], now=1000)
        vq.evaluate(processes=[_proc(used=5000)], now=1030)  # under quota → state cleared
        fires = vq.evaluate(processes=[_proc(used=20000)], now=1060)
    assert fires[0]["breached_for_s"] == 0  # fresh first-breach


def test_evaluate_term_action_sends_sigterm_after_grace():
    """action=term + grace_s < elapsed < 2*grace_s → SIGTERM only."""
    sent = []
    with tempfile.TemporaryDirectory() as td, _with_tmp_paths(td), \
         patch.object(vq, "_send_signal", lambda pid, sig: sent.append((pid, sig)) or True):
        vq.save_rules([_rule(max_mib=10000, grace_s=10, action="term")])
        vq.evaluate(processes=[_proc(used=20000)], now=1000, dry_run_global=False)
        fires = vq.evaluate(processes=[_proc(used=20000)], now=1015, dry_run_global=False)
    assert fires[0]["escalation"] == "term-sent"
    assert sent and sent[0][1] == signal.SIGTERM


def test_evaluate_term_escalates_to_kill_after_2x_grace():
    """action=term + elapsed >= 2*grace_s → SIGKILL after SIGTERM."""
    sent = []
    with tempfile.TemporaryDirectory() as td, _with_tmp_paths(td), \
         patch.object(vq, "_send_signal", lambda pid, sig: sent.append((pid, sig)) or True):
        vq.save_rules([_rule(max_mib=10000, grace_s=10, action="term")])
        vq.evaluate(processes=[_proc(used=20000)], now=1000, dry_run_global=False)
        fires = vq.evaluate(processes=[_proc(used=20000)], now=1025, dry_run_global=False)
    assert fires[0]["escalation"] == "kill-sent-after-term"
    assert signal.SIGKILL in [s[1] for s in sent]


def test_evaluate_kill_action_sends_sigkill():
    sent = []
    with tempfile.TemporaryDirectory() as td, _with_tmp_paths(td), \
         patch.object(vq, "_send_signal", lambda pid, sig: sent.append((pid, sig)) or True):
        vq.save_rules([_rule(max_mib=10000, grace_s=10, action="kill")])
        vq.evaluate(processes=[_proc(used=20000)], now=1000, dry_run_global=False)
        fires = vq.evaluate(processes=[_proc(used=20000)], now=1020, dry_run_global=False)
    assert fires[0]["escalation"] == "kill-sent"
    assert sent and sent[0][1] == signal.SIGKILL


def test_evaluate_dry_run_global_skips_signals():
    """dry_run_global=True must NOT send any signal even for action=kill."""
    sent = []
    with tempfile.TemporaryDirectory() as td, _with_tmp_paths(td), \
         patch.object(vq, "_send_signal", lambda pid, sig: sent.append((pid, sig))):
        vq.save_rules([_rule(max_mib=10000, grace_s=10, action="kill")])
        vq.evaluate(processes=[_proc(used=20000)], now=1000, dry_run_global=True)
        fires = vq.evaluate(processes=[_proc(used=20000)], now=1020, dry_run_global=True)
    assert sent == []
    assert fires[0]["escalation"] == "would-kill"


def test_evaluate_regex_match_negative():
    """A process whose name doesn't match the regex is ignored."""
    with tempfile.TemporaryDirectory() as td, _with_tmp_paths(td):
        vq.save_rules([_rule(regex="exclusive-tag", max_mib=10000, action="warn")])
        fires = vq.evaluate(processes=[_proc(used=20000)], now=1000)
    assert fires == []


# ── audit log ────────────────────────────────────────────────────────────


def test_audit_appended_on_warn_fire():
    with tempfile.TemporaryDirectory() as td, _with_tmp_paths(td):
        vq.save_rules([_rule(max_mib=10000, grace_s=10, action="warn")])
        vq.evaluate(processes=[_proc(used=20000)], now=1000)
        audit = vq.load_audit()
    assert len(audit) == 1
    assert audit[0]["pid"] == 1785


# ── status() ─────────────────────────────────────────────────────────────


def test_status_includes_rules_audit_actions():
    with tempfile.TemporaryDirectory() as td, _with_tmp_paths(td):
        vq.save_rules([_rule()])
        s = vq.status()
    assert s["ok"] is True
    assert len(s["rules"]) == 1
    assert "warn" in s["actions_supported"]
    assert "kill" in s["actions_supported"]
