"""R&D #19.1 — GPU process nice advisor tests."""
import os
import pytest
from unittest.mock import patch
from gpu_dashboard.modules import process_nice as pn


# ── classify ───────────────────────────────────────────────────────────


def test_classify_steam_is_interactive():
    cls, n = pn.classify("steam", "/usr/games/steam")
    assert cls == "interactive"
    assert n == 0


def test_classify_ollama_is_llm_serve():
    cls, n = pn.classify("ollama", "ollama serve")
    assert cls == "llm_serve"
    assert n == 5


def test_classify_llama_server():
    cls, n = pn.classify("llama-server", "llama-server -m foo.gguf")
    assert cls == "llm_serve"


def test_classify_vllm_via_cmdline():
    cls, n = pn.classify("python3",
                          "python3 -m vllm.entrypoints.openai.api_server")
    assert cls == "llm_serve"


def test_classify_deepspeed_is_llm_train():
    cls, n = pn.classify("python3", "deepspeed --num_gpus=2 train.py")
    assert cls == "llm_train"
    assert n == 10


def test_classify_blender_is_render():
    cls, n = pn.classify("blender", "/usr/bin/blender")
    assert cls == "render"
    assert n == 15


def test_classify_ffmpeg_is_encode():
    cls, n = pn.classify("ffmpeg", "ffmpeg -i in.mp4 out.mp4")
    assert cls == "encode"
    assert n == -5


def test_classify_unknown():
    cls, n = pn.classify("randomtool", "randomtool --x")
    assert cls == "unknown"
    assert n == 0


# ── _read_stat ─────────────────────────────────────────────────────────


def test_read_stat_basic(tmp_path):
    p = tmp_path / "1234"; p.mkdir()
    # Build a fake /proc/<pid>/stat line.
    # Format : pid (comm) state ppid pgrp ... priority nice ...
    # Fields after comm : index 0=state, 1=ppid, 2=pgrp, ... 15=priority, 16=nice
    fields = ["S", "1", "1234"] + ["0"] * 12 + ["20", "5"] + ["0"] * 20
    raw = f"1234 (myproc) {' '.join(fields)}\n"
    (p / "stat").write_text(raw)
    st = pn._read_stat(1234, proc_root=str(tmp_path))
    assert st is not None
    assert st["nice"] == 5
    assert st["priority"] == 20


def test_read_stat_comm_with_spaces(tmp_path):
    """Process names with spaces & parens must be handled (e.g. 'Web Content')."""
    p = tmp_path / "999"; p.mkdir()
    fields = ["S", "1", "999"] + ["0"] * 12 + ["20", "-5"] + ["0"] * 20
    raw = f"999 (Web Content (renderer)) {' '.join(fields)}\n"
    (p / "stat").write_text(raw)
    st = pn._read_stat(999, proc_root=str(tmp_path))
    assert st is not None
    assert st["nice"] == -5


def test_read_stat_missing(tmp_path):
    assert pn._read_stat(99999, proc_root=str(tmp_path)) is None


# ── advise_process ─────────────────────────────────────────────────────


def _mkproc(tmp_path, pid, comm, cmdline="", nice=0):
    p = tmp_path / str(pid); p.mkdir()
    (p / "comm").write_text(comm + "\n")
    (p / "cmdline").write_bytes(cmdline.replace(" ", "\x00").encode() + b"\x00")
    fields = ["S", "1", str(pid)] + ["0"] * 12 + ["20", str(nice)] + ["0"] * 20
    raw = f"{pid} ({comm}) {' '.join(fields)}\n"
    (p / "stat").write_text(raw)


def test_advise_needs_change(tmp_path):
    _mkproc(tmp_path, 1, "ollama", "ollama serve", nice=0)
    a = pn.advise_process(1, proc_root=str(tmp_path))
    assert a is not None
    assert a["class"] == "llm_serve"
    assert a["current_nice"] == 0
    assert a["suggested_nice"] == 5
    assert a["needs_change"] is True
    assert "renice -n 5 -p 1" in a["shell_command"]


def test_advise_no_change_when_already_good(tmp_path):
    _mkproc(tmp_path, 2, "ollama", "ollama serve", nice=5)
    a = pn.advise_process(2, proc_root=str(tmp_path))
    assert a["needs_change"] is False
    assert a["shell_command"] is None


def test_advise_unknown_no_suggestion(tmp_path):
    _mkproc(tmp_path, 3, "randomtool", "randomtool")
    a = pn.advise_process(3, proc_root=str(tmp_path))
    assert a["class"] == "unknown"
    assert a["suggested_nice"] is None
    assert a["needs_change"] is False


def test_advise_missing_pid_returns_none(tmp_path):
    assert pn.advise_process(999999, proc_root=str(tmp_path)) is None


# ── status ─────────────────────────────────────────────────────────────


def test_status_no_gpu_processes():
    with patch.object(pn, "list_gpu_compute_pids", return_value=[]):
        s = pn.status()
    assert s["needs_action_count"] == 0
    assert "no GPU compute processes" in s["reason"]


def test_status_with_compute_processes():
    with patch.object(pn, "list_gpu_compute_pids", return_value=[1, 2]):
        with patch.object(pn, "advise_process") as mock_adv:
            mock_adv.side_effect = [
                {"pid": 1, "comm": "ollama", "cmdline_short": "",
                 "class": "llm_serve", "current_nice": 0, "suggested_nice": 5,
                 "needs_change": True, "shell_command": "renice -n 5 -p 1"},
                {"pid": 2, "comm": "blender", "cmdline_short": "",
                 "class": "render", "current_nice": 15, "suggested_nice": 15,
                 "needs_change": False, "shell_command": None},
            ]
            s = pn.status()
    assert s["needs_action_count"] == 1
    assert len(s["processes"]) == 2
