"""Module tcp_congestion_control_audit — TCP CC + Fast Open
selector posture (R&D #89.1).

Two existing modules touch TCP sysctls :

  * net_proto_counters — reads counters only ; recommendation
    snippet mentions tcp_congestion_control but never reads
    the selector itself.
  * net_sysctl_audit — owns rmem/wmem/backlog/busy_poll.

Neither reads the *current* TCP congestion-control algorithm
or the available/allowed sets, nor /proc/sys/net/ipv4/
tcp_fastopen. This audit owns that gap.

Reads :

  /proc/sys/net/ipv4/tcp_congestion_control
      string, e.g. "bbr" / "cubic" / "reno"
  /proc/sys/net/ipv4/tcp_available_congestion_control
      whitespace list of CC algorithms the kernel can use
  /proc/sys/net/ipv4/tcp_fastopen
      bitfield : 0=off, 1=client, 2=server, 3=both,
      higher bits = additional TFO options.

Verdicts (worst-first) :

  bbr_available_unused   warn   "bbr" listed as available but
                                tcp_congestion_control is
                                another algorithm — bbr is
                                a big throughput win on most
                                modern uplinks ; the operator
                                left it on the table.
  tfo_off                accent  tcp_fastopen = 0 — modern
                                kernels default to >0 ;
                                explicit zero is rare and
                                kills the 1-RTT setup win.
  ok                     bbr active, OR bbr unavailable in
                         the kernel build (couldn't enable it
                         even if we tried).
  unknown                /proc/sys/net/ipv4 unreadable.

stdlib only.
"""
from __future__ import annotations

import os
from typing import Optional

NAME = "tcp_congestion_control_audit"

DEFAULT_PROC_SYS_NET = "/proc/sys/net/ipv4"


def _read_text(path: str) -> Optional[str]:
    try:
        with open(path, "r", encoding="utf-8") as fh:
            return fh.read().strip()
    except (OSError, PermissionError):
        return None


def read_tcp_state(root: str = DEFAULT_PROC_SYS_NET) -> dict:
    cc = _read_text(
        os.path.join(root, "tcp_congestion_control"))
    avail = _read_text(
        os.path.join(root, "tcp_available_congestion_control"))
    tfo = _read_text(os.path.join(root, "tcp_fastopen"))
    tfo_val: Optional[int] = None
    if tfo:
        try:
            tfo_val = int(tfo)
        except ValueError:
            tfo_val = None
    return {
        "current_cc": cc or "",
        "available_cc": (avail or "").split(),
        "tcp_fastopen": tfo_val,
    }


def classify(state: dict) -> dict:
    cc = state["current_cc"]
    avail = state["available_cc"]
    tfo = state["tcp_fastopen"]

    if not cc:
        return {"verdict": "unknown",
                "reason": (
                    "/proc/sys/net/ipv4/tcp_congestion_control "
                    "unreadable — procfs unavailable or "
                    "non-IPv4 kernel.")}

    # warn — bbr compiled in but not active
    if "bbr" in avail and cc != "bbr":
        return {
            "verdict": "bbr_available_unused",
            "reason": (
                f"tcp_congestion_control = '{cc}' but 'bbr' "
                "is in tcp_available_congestion_control — "
                "switch to bbr for measurable throughput "
                "gains on most uplinks. Apply with: echo "
                "bbr > /proc/sys/net/ipv4/tcp_congestion_control"),
            "current_cc": cc,
            "available_cc": avail,
        }

    # accent — TFO explicitly disabled
    if tfo == 0:
        return {
            "verdict": "tfo_off",
            "reason": (
                "tcp_fastopen = 0 — TFO is explicitly off ; "
                "every new TCP connection pays a full "
                "handshake RTT. Modern kernels default to 1."),
            "tcp_fastopen": tfo,
        }

    return {
        "verdict": "ok",
        "reason": (
            f"TCP CC = '{cc}' "
            + ("(bbr active)" if cc == "bbr"
               else "(bbr not compiled in)" if "bbr" not in avail
               else "")
            + f" ; tcp_fastopen = {tfo}."),
        "current_cc": cc,
    }


def status(config: Optional[dict] = None,
           proc_sys_net: str = DEFAULT_PROC_SYS_NET) -> dict:
    state = read_tcp_state(proc_sys_net)
    verdict = classify(state)
    return {
        "ok": verdict["verdict"] == "ok",
        "current_cc": state["current_cc"],
        "available_cc": state["available_cc"],
        "tcp_fastopen": state["tcp_fastopen"],
        "verdict": verdict,
    }
