"""Module kmsg_audit — kernel ring-buffer priority histogram (R&D #49.1).

Reads /proc/sys/kernel/{printk, printk_ratelimit, printk_ratelimit_
burst, printk_devkmsg, dmesg_restrict, kptr_restrict} (always
accessible) and best-effort tails /dev/kmsg (root-only on most
distros — module degrades to requires_root).

/dev/kmsg record format (newline-delimited) :
  <priority>,<seqnum>,<timestamp_usec>,<flag>[,key=val,...];<message>
  [+ key=val\\n]*

priority = facility * 8 + level. Level 0..7 :
  0 emerg / 1 alert / 2 crit / 3 err / 4 warn /
  5 notice / 6 info / 7 debug

Verdicts (priority-ordered) :
  ratelimit_drops          /proc/sys/kernel/printk_ratelimit > 0
                           + observed "*** XXX messages suppressed"
                           in tail → kernel rate-limit dropped logs.
  loud_kernel              ≥ 5 err/warn records in the most-recent
                           sampled window → something noisy in the
                           kernel right now (driver flapping, MCE,
                           taint).
  ok                       printk readable, no recent err/warn,
                           ratelimit clean.
  requires_root            /dev/kmsg not readable — daemon needs
                           CAP_SYSLOG or root.
  unknown                  /proc/sys/kernel/printk also unreadable.

stdlib only.
"""
from __future__ import annotations

import os
from typing import Optional


NAME = "kmsg_audit"


_PROC_SYS_KERNEL = "/proc/sys/kernel"
_DEV_KMSG = "/dev/kmsg"


LEVEL_NAMES = {
    0: "emerg", 1: "alert", 2: "crit", 3: "err",
    4: "warn", 5: "notice", 6: "info", 7: "debug",
}


def _read(p: str) -> Optional[str]:
    try:
        with open(p) as f:
            return f.read()
    except OSError:
        return None


def _read_int(p: str) -> Optional[int]:
    t = _read(p)
    if t is None:
        return None
    try:
        return int(t.strip())
    except ValueError:
        return None


def parse_printk(text: Optional[str]) -> dict:
    """/proc/sys/kernel/printk → 4 ints tab-separated :
       console_loglevel default_message_loglevel
       minimum_console_loglevel default_console_loglevel."""
    if not text:
        return {}
    parts = text.split()
    if len(parts) < 4:
        return {}
    try:
        return {
            "console_loglevel": int(parts[0]),
            "default_message_loglevel": int(parts[1]),
            "minimum_console_loglevel": int(parts[2]),
            "default_console_loglevel": int(parts[3]),
        }
    except ValueError:
        return {}


def parse_kmsg_line(line: str) -> Optional[dict]:
    """Parse one /dev/kmsg record. Returns {priority, level,
    facility, seq, timestamp_usec, message} or None on malformed."""
    if not line or ";" not in line:
        return None
    header, _, message = line.partition(";")
    fields = header.split(",")
    if len(fields) < 4:
        return None
    try:
        priority = int(fields[0])
        seq = int(fields[1])
        ts_us = int(fields[2])
    except ValueError:
        return None
    return {
        "priority": priority,
        "level": priority & 0x07,
        "facility": (priority >> 3) & 0xff,
        "seq": seq,
        "timestamp_usec": ts_us,
        "message": message.rstrip("\n"),
    }


def tail_kmsg(dev_kmsg: str = _DEV_KMSG,
                max_records: int = 500) -> dict:
    """Returns {available, permission_error, records_read,
    suppressed_count, by_level: {level: count}}."""
    out: dict = {"available": False, "permission_error": False,
                  "records_read": 0, "suppressed_count": 0,
                  "by_level": {}}
    try:
        fd = os.open(dev_kmsg, os.O_RDONLY | os.O_NONBLOCK)
    except PermissionError:
        out["permission_error"] = True
        return out
    except OSError:
        return out
    out["available"] = True
    try:
        buf = b""
        # Drain in 8 KiB chunks ; stop when we hit EAGAIN.
        for _ in range(64):
            try:
                chunk = os.read(fd, 8192)
            except BlockingIOError:
                break
            except OSError:
                break
            if not chunk:
                break
            buf += chunk
            if buf.count(b"\n") >= max_records * 2:
                break
        records = buf.decode("utf-8", errors="replace").splitlines()
        n = 0
        by_lvl: dict = {}
        suppressed = 0
        for line in records:
            if n >= max_records:
                break
            r = parse_kmsg_line(line)
            if not r:
                continue
            n += 1
            lvl = r["level"]
            by_lvl[lvl] = by_lvl.get(lvl, 0) + 1
            msg = (r["message"] or "").lower()
            if "messages suppressed" in msg:
                suppressed += 1
        out["records_read"] = n
        out["by_level"] = by_lvl
        out["suppressed_count"] = suppressed
    finally:
        try:
            os.close(fd)
        except OSError:
            pass
    return out


_RECIPE_RATE_LIMIT = (
    "# Kernel rate-limit dropped log records. Bump the burst :\n"
    "echo 100 | sudo tee /proc/sys/kernel/printk_ratelimit_burst\n"
    "echo 'kernel.printk_ratelimit_burst = 100' | \\\n"
    "  sudo tee /etc/sysctl.d/99-printk.conf\n"
    "sudo sysctl --system"
)

_RECIPE_LOUD = (
    "# Multiple err/warn records in recent kernel ring buffer.\n"
    "# Investigate via journalctl :\n"
    "journalctl -k -p err -n 100 --no-pager\n"
    "# Common culprits : driver flapping, MCE, GPU resets, NIC\n"
    "# link-down events."
)

_RECIPE_REQUIRES_ROOT = (
    "# /dev/kmsg is 0440 root-only on most distros. To grant\n"
    "# the daemon read access without running it as root :\n"
    "systemctl --user edit gpu-dashboard.service\n"
    "# [Service]\n"
    "# AmbientCapabilities=CAP_SYSLOG\n"
    "systemctl --user daemon-reload\n"
    "systemctl --user restart gpu-dashboard.service"
)


_LOUD_THRESHOLD = 5  # err or warn records to flag


def classify(printk: dict, kmsg: dict) -> dict:
    if not printk:
        return {"verdict": "unknown",
                "reason": "/proc/sys/kernel/printk unreadable.",
                "recommendation": ""}
    if not kmsg.get("available"):
        return {"verdict": "requires_root",
                "reason": ("/dev/kmsg unreadable — daemon needs "
                           "CAP_SYSLOG or root."),
                "recommendation": _RECIPE_REQUIRES_ROOT}
    if (kmsg.get("suppressed_count") or 0) > 0:
        return {"verdict": "ratelimit_drops",
                "reason": (f"Observed {kmsg['suppressed_count']} "
                           f"'messages suppressed' record(s) in "
                           f"the recent ring buffer — kernel rate-"
                           f"limit dropped logs."),
                "recommendation": _RECIPE_RATE_LIMIT}
    by_lvl = kmsg.get("by_level") or {}
    loud = (by_lvl.get(0, 0) + by_lvl.get(1, 0)
              + by_lvl.get(2, 0) + by_lvl.get(3, 0)
              + by_lvl.get(4, 0))
    if loud >= _LOUD_THRESHOLD:
        labels = ", ".join(f"{LEVEL_NAMES.get(k, k)}={v}"
                            for k, v in sorted(by_lvl.items())
                            if k <= 4 and v > 0)
        return {"verdict": "loud_kernel",
                "reason": (f"{loud} err/warn-level records in "
                           f"last {kmsg.get('records_read', 0)} "
                           f"sampled ({labels})."),
                "recommendation": _RECIPE_LOUD}
    return {"verdict": "ok",
            "reason": (f"console_loglevel="
                       f"{printk.get('console_loglevel')}, "
                       f"{kmsg.get('records_read', 0)} kmsg records "
                       f"sampled, no suppressed-message events, "
                       f"≤ 4 err/warn entries."),
            "recommendation": ""}


def status(cfg=None) -> dict:
    printk_text = _read(os.path.join(_PROC_SYS_KERNEL, "printk"))
    printk = parse_printk(printk_text)
    rate_int = _read_int(os.path.join(_PROC_SYS_KERNEL,
                                          "printk_ratelimit"))
    rate_burst = _read_int(os.path.join(_PROC_SYS_KERNEL,
                                           "printk_ratelimit_burst"))
    dmesg_restrict = _read_int(os.path.join(_PROC_SYS_KERNEL,
                                                 "dmesg_restrict"))
    kmsg = tail_kmsg(_DEV_KMSG)
    verdict = classify(printk, kmsg)
    return {
        "ok": bool(printk) or kmsg.get("available", False),
        "printk": printk,
        "printk_ratelimit_sec": rate_int,
        "printk_ratelimit_burst": rate_burst,
        "dmesg_restrict": dmesg_restrict,
        "kmsg": kmsg,
        "verdict": verdict,
    }
