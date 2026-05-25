"""Module ima_measurement_freshness_audit — IMA runtime
measurement log content + freshness (R&D #104.4).

IMA produces a hash-chain log every measured execve / mmap.
The existing ima_integrity_audit reads only the counters
(runtime_measurements_count, violations, policy). It never
opens the log itself — so it can't tell whether:

  - the count says 'N' but the log is empty / unreadable
    (kernel <-> securityfs drift)
  - the boot_aggregate (PCR10 anchor) is present, linking
    measurements to TPM
  - the log has more than 1 line (any measurement at all)

Reads :

  /sys/kernel/security/ima/runtime_measurements_count
  /sys/kernel/security/ima/ascii_runtime_measurements
                                                 (root-only)

Verdicts (worst-first) :

  ima_log_missing             err     count > 0 but the log
                                      file is empty — kernel
                                      / securityfs out of
                                      sync.
  ima_boot_aggregate_absent   warn    log present but no
                                      boot_aggregate / PCR10
                                      anchor — measurements
                                      not chained to TPM.
  ima_log_empty               accent  count == 0 — IMA loaded
                                      but no policy active or
                                      policy didn't match
                                      anything.
  ok                                  log + anchor present.
  requires_root                       log unreadable as user.
  unknown                             /sys/kernel/security/ima
                                      absent (CONFIG_IMA=n).

stdlib only.
"""
from __future__ import annotations

import os
from typing import Optional

NAME = "ima_measurement_freshness_audit"

DEFAULT_IMA = "/sys/kernel/security/ima"


def _read_text(path: str) -> Optional[str]:
    try:
        with open(path, "r", encoding="utf-8") as fh:
            return fh.read()
    except (OSError, PermissionError):
        return None


def _read_int(path: str) -> Optional[int]:
    t = _read_text(path)
    if t is None:
        return None
    try:
        return int(t.strip())
    except ValueError:
        return None


def parse_log(text: Optional[str]) -> dict:
    """Return {line_count, has_boot_aggregate}."""
    out = {"line_count": 0, "has_boot_aggregate": False}
    if not text:
        return out
    for line in text.splitlines():
        if not line.strip():
            continue
        out["line_count"] += 1
        if "boot_aggregate" in line:
            out["has_boot_aggregate"] = True
    return out


def classify(ima_present: bool,
             count: Optional[int],
             log_readable: bool,
             log_info: dict) -> dict:
    if not ima_present:
        return {"verdict": "unknown",
                "reason": (
                    "/sys/kernel/security/ima absent — "
                    "CONFIG_IMA=n.")}
    if not log_readable:
        return {"verdict": "requires_root",
                "reason": (
                    "ascii_runtime_measurements unreadable "
                    "— re-run as root.")}

    line_count = log_info.get("line_count", 0)
    has_anchor = log_info.get("has_boot_aggregate", False)

    # err — count > 0 but log empty
    if (count is not None and count > 0
            and line_count == 0):
        return {
            "verdict": "ima_log_missing",
            "reason": (
                f"runtime_measurements_count={count} but "
                "ascii_runtime_measurements log is empty. "
                "Kernel / securityfs out of sync.")}

    # warn — no boot_aggregate anchor
    if line_count > 0 and not has_anchor:
        return {
            "verdict": "ima_boot_aggregate_absent",
            "reason": (
                f"Log has {line_count} entries but no "
                "boot_aggregate (PCR10) anchor. "
                "Measurements not chained to TPM ; chain "
                "of trust broken.")}

    # accent — IMA loaded but nothing measured
    if count == 0 and line_count == 0:
        return {
            "verdict": "ima_log_empty",
            "reason": (
                "IMA exposed but runtime_measurements_count"
                "=0 and log empty — no policy active or "
                "policy matched nothing.")}

    return {"verdict": "ok",
            "reason": (
                f"count={count} ; log_lines={line_count} ; "
                f"boot_aggregate={has_anchor}. Healthy.")}


def status(config: Optional[dict] = None,
           ima: str = DEFAULT_IMA) -> dict:
    ima_present = os.path.isdir(ima)
    count = _read_int(
        os.path.join(ima, "runtime_measurements_count"))
    log_text = _read_text(
        os.path.join(ima, "ascii_runtime_measurements"))
    log_readable = (
        log_text is not None
        if ima_present else False)
    log_info = parse_log(log_text)
    verdict = classify(ima_present, count, log_readable,
                       log_info)
    return {
        "ok": verdict["verdict"] == "ok",
        "runtime_measurements_count": count,
        "log_line_count": log_info["line_count"],
        "has_boot_aggregate": log_info["has_boot_aggregate"],
        "verdict": verdict,
    }
