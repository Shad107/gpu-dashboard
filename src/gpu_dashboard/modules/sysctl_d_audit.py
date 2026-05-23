"""Module sysctl_d_audit — sysctl.d on-disk vs runtime drift (R&D #39.2).

Sibling of shipped #38.2 modprobe_audit: this module catches the
"user edited /etc/sysctl.d/99-llm.conf with `vm.swappiness=10` but
forgot to run `sudo sysctl --system`" foot-gun. Shipped #32.4
vm_sysctl_audit sees the wrong runtime value ; this module sees
the *intent* and surfaces it as drift.

Reads every key=value line from (in sysctl --system order):

  /etc/sysctl.conf
  /etc/sysctl.d/*.conf
  /run/sysctl.d/*.conf
  /usr/lib/sysctl.d/*.conf
  /usr/local/lib/sysctl.d/*.conf

Compares each on-disk value to its /proc/sys/<key with dots →
slashes> runtime counterpart.

Verdicts:
  synced       every on-disk value matches runtime
  drift        ≥1 mismatch — recipe is `sudo sysctl --system`
  no_config    no .conf files in any sysctl.d dir
  unknown      /proc/sys unreadable

drift_rows expose {key, on_disk, runtime, files} so the user can
spot exactly which key needs the reload.

stdlib only.
"""
from __future__ import annotations

import os
import re
from typing import Optional


NAME = "sysctl_d_audit"


# sysctl --system applies these directories in lexicographic order
# across files, with later dirs taking precedence:
_SYSCTL_DIRS = [
    "/etc/sysctl.conf",     # treated as a file ; legacy
    "/run/sysctl.d",
    "/etc/sysctl.d",
    "/usr/local/lib/sysctl.d",
    "/usr/lib/sysctl.d",
]
_PROC_SYS = "/proc/sys"


_LINE_RE = re.compile(r"^\s*-?\s*([\w.\-/]+)\s*=\s*(.+?)\s*$")


def parse_sysctl_line(line: str) -> Optional[dict]:
    if not line:
        return None
    s = line.strip()
    if not s or s.startswith("#") or s.startswith(";"):
        return None
    m = _LINE_RE.match(s)
    if not m:
        return None
    return {"key": m.group(1), "value": m.group(2).strip()}


def parse_sysctl_file(text: str) -> list:
    if not text:
        return []
    out: list = []
    for line in text.splitlines():
        rec = parse_sysctl_line(line)
        if rec:
            out.append(rec)
    return out


def collect_settings_from_dirs(dirs: list) -> dict:
    """Walk every .conf in `dirs` in order, with later entries
    overriding earlier ones. Returns {key: {value, files}}."""
    out: dict = {}
    for d in dirs:
        if os.path.isfile(d):
            # /etc/sysctl.conf single-file case
            paths = [d]
        elif os.path.isdir(d):
            try:
                names = sorted(os.listdir(d))
            except OSError:
                continue
            paths = [os.path.join(d, n) for n in names
                       if n.endswith(".conf")]
        else:
            continue
        for path in paths:
            try:
                with open(path) as f:
                    text = f.read()
            except OSError:
                continue
            for rec in parse_sysctl_file(text):
                entry = out.setdefault(rec["key"],
                                          {"value": "", "files": []})
                entry["value"] = rec["value"]
                if path not in entry["files"]:
                    entry["files"].append(path)
    return out


def read_runtime_value(proc_sys: str, key: str) -> Optional[str]:
    p = os.path.join(proc_sys, *key.split("."))
    try:
        with open(p) as f:
            raw = f.read().strip()
    except OSError:
        return None
    # Collapse whitespace so "4096\t131072\t33554432" matches a config
    # line that wrote the same in spaces.
    return " ".join(raw.split())


_RECIPE_DRIFT = (
    "# /etc/sysctl.d/*.conf disagrees with runtime — kernel hasn't\n"
    "# reloaded sysctl values since the file was edited. Apply now:\n"
    "sudo sysctl --system\n"
    "# Verify by re-checking this card or running\n"
    "sudo sysctl -p /etc/sysctl.d/99-llm.conf   # specific file\n"
    "# Companion: #32.4 vm_sysctl_audit (runtime view of vm.*)."
)


def _normalize(value: str) -> str:
    return " ".join((value or "").split())


def classify(on_disk: dict, runtime: dict) -> dict:
    if not on_disk:
        return {"verdict": "no_config",
                "reason": ("No key=value lines in any sysctl.d location "
                           "(/etc/sysctl.conf or /etc/sysctl.d/*.conf, "
                           "/run/sysctl.d/, /usr/lib/sysctl.d/). System "
                           "is on kernel defaults."),
                "recommendation": ""}
    drift_rows: list = []
    matched = 0
    for key, conf in on_disk.items():
        want = _normalize(conf["value"])
        got_raw = runtime.get(key)
        if got_raw is None:
            # Setting in sysctl.d but no /proc/sys entry — skip (often
            # a setting that's loaded by a sub-system not yet active,
            # or a /proc/sys entry that doesn't exist on this kernel)
            continue
        got = _normalize(got_raw)
        if got != want:
            drift_rows.append({"key": key, "on_disk": want,
                                "runtime": got, "files": conf["files"]})
        else:
            matched += 1
    if drift_rows:
        keys = ", ".join(r["key"] for r in drift_rows[:3])
        return {"verdict": "drift",
                "reason": (f"{len(drift_rows)} sysctl(s) on-disk differ "
                           f"from runtime: {keys}. Run `sudo sysctl "
                           f"--system` to apply."),
                "recommendation": _RECIPE_DRIFT,
                "drift_rows": drift_rows}
    return {"verdict": "synced",
            "reason": f"All {matched} on-disk sysctl(s) match runtime.",
            "recommendation": ""}


def status(cfg=None) -> dict:
    on_disk = collect_settings_from_dirs(_SYSCTL_DIRS)
    runtime: dict = {}
    for key in on_disk:
        v = read_runtime_value(_PROC_SYS, key)
        if v is not None:
            runtime[key] = v
    verdict = classify(on_disk, runtime)
    return {
        "ok": True,
        "on_disk_count": len(on_disk),
        "on_disk": on_disk,
        "runtime": runtime,
        "verdict": verdict,
        "drift_rows": verdict.get("drift_rows", []),
    }
