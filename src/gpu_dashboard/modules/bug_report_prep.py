"""Module bug_report_prep — NVIDIA bug-report ticket prepper (R&D #25.3).

When a user hits an XID crash or GSP fault, NVIDIA's bug-report
process expects :
  1. `sudo nvidia-bug-report.sh` output (~50 MB tarball)
  2. A clear description of the symptom + repro steps
  3. System context (driver, kernel, GPU model)

Most users never reach step 1 — they file confused issues on forums
instead. This module bridges the gap by pre-filling a ready-to-paste
bug-report template using data the dashboard already collects :

  - shipped XID decoder (#14.x) — recent crashes + decoded meanings
  - shipped GSP surfacer (#21.3) — driver fallback / firmware events
  - shipped procfs deep-state (#23.6) — model, VBIOS, GSP firmware
  - shipped DKMS status (#24.3) — driver flavor + kernel version

The template includes a "TODO" placeholder for the user's symptom
narrative, then concrete next-step commands (run nvidia-bug-report.sh
and attach to dr_bundle).

Pure aggregator — no new I/O, just composes data from sibling modules.
"""
from __future__ import annotations

import os
import platform
from typing import Optional


NAME = "bug_report_prep"


def _safe_call(callable_, *args, **kwargs):
    """Call a sibling-module function, return None on any exception."""
    try:
        return callable_(*args, **kwargs)
    except Exception:
        return None


def gather_system_context() -> dict:
    """Best-effort system info pull from sibling modules."""
    try:
        from . import xid_decoder
    except ImportError:
        xid_decoder = None  # type: ignore
    try:
        from . import gsp_status
    except ImportError:
        gsp_status = None  # type: ignore
    try:
        from . import proc_deep_state
    except ImportError:
        proc_deep_state = None  # type: ignore
    try:
        from . import dkms_status
    except ImportError:
        dkms_status = None  # type: ignore
    try:
        from . import driver_flavor
    except ImportError:
        driver_flavor = None  # type: ignore

    kernel = ""
    try:
        kernel = platform.uname().release
    except Exception:
        pass

    xid_events: list = []
    if xid_decoder:
        out = _safe_call(xid_decoder.decode_recent_journal,
                          since="7 days ago", limit=50)
        if isinstance(out, list):
            xid_events = out

    gsp_events: list = []
    gsp_verdict: dict = {}
    if gsp_status:
        gsp = _safe_call(gsp_status.status) or {}
        gsp_events = gsp.get("gsp_events", []) if isinstance(gsp, dict) else []
        gsp_verdict = gsp.get("verdict", {}) if isinstance(gsp, dict) else {}

    gpus: list = []
    if proc_deep_state:
        pds = _safe_call(proc_deep_state.status) or {}
        gpus = pds.get("gpus", []) if isinstance(pds, dict) else []

    dkms: dict = {}
    if dkms_status:
        d = _safe_call(dkms_status.status) or {}
        if isinstance(d, dict):
            dkms = {"running_kernel": d.get("running_kernel"),
                    "verdict": d.get("verdict", {})}

    flavor: dict = {}
    if driver_flavor:
        f = _safe_call(driver_flavor.status) or {}
        if isinstance(f, dict):
            flavor = {"kernel_module_version": f.get("kernel_module_version"),
                      "flavor": f.get("flavor")}

    return {
        "kernel": kernel,
        "xid_events_7d": xid_events,
        "gsp_events": gsp_events,
        "gsp_verdict": gsp_verdict,
        "gpus": gpus,
        "dkms": dkms,
        "driver": flavor,
    }


def compose_template(ctx: dict) -> str:
    """Render the human-readable bug-report template."""
    lines: list = []
    lines.append("============================================================")
    lines.append("  NVIDIA bug-report ticket — pre-fill from gpu-dashboard")
    lines.append("============================================================")
    lines.append("")
    lines.append("## System")
    lines.append(f"- Kernel       : {ctx.get('kernel') or '?'}")
    drv = ctx.get("driver", {}) or {}
    lines.append(f"- Driver       : {drv.get('kernel_module_version') or '?'} "
                  f"({drv.get('flavor') or '?'})")
    dkms = ctx.get("dkms", {}) or {}
    dkms_verdict = dkms.get("verdict", {}) if isinstance(dkms, dict) else {}
    lines.append(f"- DKMS state   : {dkms_verdict.get('verdict', '?')}")
    lines.append("")
    lines.append("## GPUs")
    for g in ctx.get("gpus", []):
        lines.append(f"- {g.get('model', '?')}  "
                      f"VBIOS={g.get('video_bios','?')}  "
                      f"GSP={g.get('gpu_firmware','?')}")
    if not ctx.get("gpus"):
        lines.append("- (no GPUs visible to /proc/driver/nvidia)")
    lines.append("")
    lines.append("## Recent XID events (last 7 days)")
    xid = ctx.get("xid_events_7d", [])
    if not xid:
        lines.append("- (none)")
    else:
        for e in xid[-10:]:
            if isinstance(e, dict):
                code = e.get("code", "?")
                ts = e.get("ts", "?")
                meaning = e.get("meaning", e.get("name", ""))
                lines.append(f"- Xid {code}  {ts}  — {meaning}")
    lines.append("")
    lines.append("## GSP-RM events")
    gsp_v = ctx.get("gsp_verdict", {}) or {}
    lines.append(f"- Verdict : {gsp_v.get('verdict', 'unknown')}  "
                  f"({gsp_v.get('reason', '')})")
    for e in (ctx.get("gsp_events") or [])[-5:]:
        if isinstance(e, dict):
            lines.append(f"- [{e.get('kind','?')}] {e.get('line','')}")
    lines.append("")
    lines.append("## What I was doing when the crash happened")
    lines.append("  TODO — describe symptom + reproduction steps here.")
    lines.append("")
    lines.append("## How to attach the full log")
    lines.append("  sudo nvidia-bug-report.sh    # produces /tmp/nvidia-bug-report.log.gz")
    lines.append("  # then attach that file to the GitHub issue / forum post")
    lines.append("")
    lines.append("## Suggested next steps")
    if xid:
        lines.append("- XID events are present — include the dmesg.txt section "
                      "from the bug-report.")
    if gsp_v.get("verdict") in ("crashed", "fallback"):
        lines.append("- GSP looks unhealthy. Reload nvidia modules before "
                      "blaming the application.")
    if not xid and not gsp_v.get("verdict") in ("crashed", "fallback"):
        lines.append("- No obvious crash signals. Capture the bug-report DURING "
                      "the crash for the report to be useful.")
    return "\n".join(lines)


def status(cfg=None) -> dict:
    """Aggregate snapshot."""
    ctx = gather_system_context()
    template = compose_template(ctx)
    return {
        "ok": True,
        "context_summary": {
            "kernel": ctx.get("kernel"),
            "xid_event_count": len(ctx.get("xid_events_7d", [])),
            "gsp_event_count": len(ctx.get("gsp_events", [])),
            "gpu_count": len(ctx.get("gpus", [])),
            "dkms_verdict": (ctx.get("dkms", {}) or {})
                              .get("verdict", {}).get("verdict"),
            "driver_flavor": (ctx.get("driver", {}) or {}).get("flavor"),
        },
        "template_text": template,
        "bug_report_command": "sudo nvidia-bug-report.sh",
    }
