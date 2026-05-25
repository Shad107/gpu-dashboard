"""Module rseq_kernel_audit — restartable sequences kernel
posture (R&D #99.4).

glibc 2.35+ uses restartable sequences (rseq) for fast per-CPU
data structures in malloc/tcmalloc. A kernel without rseq
forces glibc to fall back to slower locking — roughly 15-30 %
overhead on highly-threaded LLM launchers / training scripts.

The user-visible kernel posture is straightforward :

  CONFIG_RSEQ          y/n     does the kernel expose rseq(2) ?
  CONFIG_DEBUG_RSEQ    y/n     does /proc/<pid>/status expose
                                Rseq + Rseq_sig fields ?
  CONFIG_FUTEX_PI      y/n     priority-inheritance futexes
                                (rt-pi mutex glibc fallback)

The per-process Rseq fields the original survey proposed only
exist with CONFIG_DEBUG_RSEQ=y, which is off in every shipped
production kernel — including this one. So we audit the kernel
posture only, not per-task registration.

Existing modules : proc_status_caps_audit reads /proc/<pid>/status
but only Cap* ; proc_syscall_auxv_audit reads auxv ;
pipe_mqueue_limits_audit covers futex *limits* not rseq.

Reads :

  /boot/config-<uname -r>            (preferred)
  /proc/config.gz                    (fallback)
  /proc/kallsyms                     (probe rseq_syscall ; root-only)

Verdicts (worst-first) :

  rseq_kernel_disabled    warn    CONFIG_RSEQ=n — every
                                  glibc-2.35+ binary takes
                                  a malloc/locking slowdown.
  futex_pi_disabled       accent  CONFIG_FUTEX_PI=n —
                                  priority-inheritance
                                  mutexes degrade to plain
                                  futex ; latency-jitter
                                  hazard for realtime audio.
  ok                              CONFIG_RSEQ=y +
                                  CONFIG_FUTEX_PI=y.
  requires_root                   config files unreadable.
  unknown                         no kernel config found.

stdlib only.
"""
from __future__ import annotations

import gzip
import os
from typing import Optional

NAME = "rseq_kernel_audit"


def _read_text(path: str) -> Optional[str]:
    try:
        with open(path, "r", encoding="utf-8") as fh:
            return fh.read()
    except (OSError, PermissionError, UnicodeDecodeError):
        return None


def _read_gz(path: str) -> Optional[str]:
    try:
        with gzip.open(path, "rt", encoding="utf-8") as fh:
            return fh.read()
    except (OSError, gzip.BadGzipFile, UnicodeDecodeError):
        return None


def find_kernel_config(uname: str,
                        boot_dir: str = "/boot",
                        proc_config: str = "/proc/config.gz"
                        ) -> Optional[str]:
    """Return the kernel config text or None.

    Tries /boot/config-<uname> first, then /proc/config.gz.
    """
    p = os.path.join(boot_dir, f"config-{uname}")
    text = _read_text(p)
    if text is not None:
        return text
    return _read_gz(proc_config)


def parse_config_options(text: Optional[str],
                          keys: tuple) -> dict:
    """Return {key: 'y'/'m'/'n'} for each requested CONFIG_*."""
    out: dict = {k: None for k in keys}
    if not text:
        return out
    for line in text.splitlines():
        if line.startswith("# ") and line.endswith(
                " is not set"):
            # '# CONFIG_FOO is not set'
            name = line[2:-len(" is not set")]
            if name in out:
                out[name] = "n"
            continue
        if "=" in line and line.startswith("CONFIG_"):
            name, val = line.split("=", 1)
            if name in out:
                out[name] = val.strip().strip('"').lower()
    return out


def classify(config_present: bool,
             config_readable: bool,
             rseq: Optional[str],
             debug_rseq: Optional[str],
             futex_pi: Optional[str]) -> dict:
    if not config_present:
        return {"verdict": "unknown",
                "reason": (
                    "No kernel config found "
                    "(/boot/config-* and /proc/config.gz "
                    "both absent).")}
    if not config_readable:
        return {"verdict": "requires_root",
                "reason": (
                    "Kernel config unreadable — re-run "
                    "as root.")}

    # warn — rseq disabled in kernel
    if rseq == "n":
        return {
            "verdict": "rseq_kernel_disabled",
            "reason": (
                "CONFIG_RSEQ=n — kernel built without "
                "restartable sequences. glibc 2.35+ "
                "binaries fall back to slower malloc "
                "locking (~15-30 % overhead on threaded "
                "workloads).")}

    # accent — futex_pi disabled
    if futex_pi == "n":
        return {
            "verdict": "futex_pi_disabled",
            "reason": (
                "CONFIG_FUTEX_PI=n — priority-inheritance "
                "futexes unavailable. rt-mutex glibc "
                "fallback adds latency jitter ; bad for "
                "realtime audio / low-latency workloads.")}

    return {"verdict": "ok",
            "reason": (
                f"CONFIG_RSEQ={rseq} ; "
                f"CONFIG_FUTEX_PI={futex_pi} ; "
                f"CONFIG_DEBUG_RSEQ={debug_rseq}. Posture "
                "coherent.")}


def status(config: Optional[dict] = None,
           uname: Optional[str] = None,
           boot_dir: str = "/boot",
           proc_config: str = "/proc/config.gz") -> dict:
    if uname is None:
        uname = os.uname().release

    text = find_kernel_config(uname, boot_dir, proc_config)
    config_present = (
        os.path.isfile(os.path.join(
            boot_dir, f"config-{uname}"))
        or os.path.isfile(proc_config))
    config_readable = text is not None

    keys = ("CONFIG_RSEQ", "CONFIG_DEBUG_RSEQ",
            "CONFIG_FUTEX_PI")
    opts = parse_config_options(text, keys)
    verdict = classify(
        config_present, config_readable,
        opts["CONFIG_RSEQ"], opts["CONFIG_DEBUG_RSEQ"],
        opts["CONFIG_FUTEX_PI"])
    return {
        "ok": verdict["verdict"] == "ok",
        "uname": uname,
        "CONFIG_RSEQ": opts["CONFIG_RSEQ"],
        "CONFIG_DEBUG_RSEQ": opts["CONFIG_DEBUG_RSEQ"],
        "CONFIG_FUTEX_PI": opts["CONFIG_FUTEX_PI"],
        "verdict": verdict,
    }
