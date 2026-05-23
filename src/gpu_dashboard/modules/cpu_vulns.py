"""Module cpu_vulns — CPU vulnerabilities mitigation cost audit (R&D #37.1).

Linux exposes per-CPU-vulnerability state under
`/sys/devices/system/cpu/vulnerabilities/<name>`. Each file's content
follows one of three patterns:

  "Not affected"          → CPU/microcode immune, no cost
  "Mitigation: <text>"    → kernel applied a software / firmware
                            mitigation (cost varies, 1-20% on hot
                            paths for inference)
  "Vulnerable: <text>"    → no mitigation active (either by user
                            choice via mitigations=off, or because
                            no microcode patch exists yet)

For LLM rigs the aggregate cost matters more than individual flags.
Spectre-v2 IBRS alone costs ~5% on indirect-branch-heavy code
(llama.cpp's sampling loop is exactly that). MDS mitigations cost
~3-15% on prompt-processing (lots of small memory reads). Combined
modern Intel hosts pay 10-20% on LLM inference for the
default-mitigations posture.

This module enumerates all vulnerabilities/<name> files, parses each,
counts states, and emits:

  clean        all "Not affected" — best (Apple Silicon, recent AMD)
  mitigated    no vulnerables, ≥1 mitigation active — typical
                Intel host with applied mitigations
  vulnerable   at least one "Vulnerable" entry — surface decision:
                accept (recipe documents `mitigations=off` opt-out),
                or fix (microcode update via #36.1 cpu_microcode)
  unknown      sysfs absent

stdlib only.
"""
from __future__ import annotations

import os
from typing import Optional


NAME = "cpu_vulns"


_VULN_ROOT = "/sys/devices/system/cpu/vulnerabilities"


def _read(p: str) -> Optional[str]:
    try:
        with open(p) as f:
            return f.read().strip()
    except OSError:
        return None


def parse_state(text: str) -> dict:
    if not text:
        return {"state": "unknown", "detail": ""}
    s = text.strip()
    if s == "Not affected" or s.startswith("Not affected"):
        return {"state": "not_affected", "detail": ""}
    if s.startswith("Mitigation:"):
        return {"state": "mitigated", "detail": s[len("Mitigation:"):].strip()}
    if s == "Vulnerable" or s.startswith("Vulnerable"):
        # "Vulnerable" alone, or "Vulnerable: <reason>"
        if ":" in s:
            return {"state": "vulnerable", "detail": s.split(":", 1)[1].strip()}
        return {"state": "vulnerable", "detail": ""}
    return {"state": "unknown", "detail": s}


def list_vulns(root: str = _VULN_ROOT) -> list:
    try:
        names = sorted(os.listdir(root))
    except OSError:
        return []
    return [n for n in names
             if os.path.isfile(os.path.join(root, n))]


_RECIPE_MITIGATIONS_OFF = (
    "# Air-gapped / single-user LLM rig?\n"
    "# You can opt out of mitigations entirely — accept the security\n"
    "# trade-off in exchange for ~10-20 % more on prompt-processing.\n"
    "# Edit /etc/default/grub, add to GRUB_CMDLINE_LINUX_DEFAULT:\n"
    "#   mitigations=off\n"
    "sudo update-grub && reboot\n"
    "# After reboot, re-check this card — most lines should flip to\n"
    "# `Vulnerable: Mitigations disabled by command line.`"
)

_RECIPE_FIX_VULNERABLE = (
    "# `Vulnerable: No microcode` means the CPU vendor has not\n"
    "# released a microcode patch yet, OR you're on stale microcode.\n"
    "# Check #36.1 cpu_microcode for revision drift, then:\n"
    "sudo apt update && sudo apt install --reinstall intel-microcode\n"
    "sudo update-initramfs -u && sudo reboot"
)


def classify(rows: list) -> dict:
    if not rows:
        return {"verdict": "unknown",
                "reason": "No vulnerabilities/<name> files readable.",
                "recommendation": ""}
    states = [r["state"] for r in rows]
    vulnerable = [r for r in rows if r["state"] == "vulnerable"]
    mitigated = [r for r in rows if r["state"] == "mitigated"]
    if vulnerable:
        names = sorted(r["name"] for r in vulnerable)
        return {"verdict": "vulnerable",
                "reason": (f"{len(vulnerable)} vulnerability/ies have no "
                           f"active mitigation: {', '.join(names)}. "
                           f"Either accept the exposure (air-gapped LLM "
                           f"rig — recipe below) or get the microcode."),
                "recommendation": _RECIPE_FIX_VULNERABLE}
    if all(s == "not_affected" for s in states):
        return {"verdict": "clean",
                "reason": (f"All {len(rows)} CPU vulnerabilities are "
                           f"`Not affected` — no mitigation cost paid."),
                "recommendation": ""}
    if mitigated:
        names = sorted(r["name"] for r in mitigated)
        return {"verdict": "mitigated",
                "reason": (f"{len(mitigated)} active mitigation(s): "
                           f"{', '.join(names)}. Typical aggregate cost: "
                           f"~10-20 % on LLM prompt-processing for an "
                           f"Intel host."),
                "recommendation": _RECIPE_MITIGATIONS_OFF}
    return {"verdict": "unknown",
            "reason": "Vulnerability files present but unparseable.",
            "recommendation": ""}


def status(cfg=None) -> dict:
    if not os.path.isdir(_VULN_ROOT):
        return {"ok": False, "error": "vulns_unavailable",
                "reason": f"{_VULN_ROOT} not present."}
    rows: list = []
    counts = {"not_affected": 0, "mitigated": 0,
              "vulnerable": 0, "unknown": 0}
    for name in list_vulns(_VULN_ROOT):
        text = _read(os.path.join(_VULN_ROOT, name)) or ""
        parsed = parse_state(text)
        row = {"name": name, **parsed, "raw": text}
        rows.append(row)
        counts[parsed["state"]] = counts.get(parsed["state"], 0) + 1
    verdict = classify(rows)
    return {
        "ok": True,
        "vulnerability_count": len(rows),
        "counts": counts,
        "rows": rows,
        "verdict": verdict,
    }
