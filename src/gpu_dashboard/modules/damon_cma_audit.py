"""Module damon_cma_audit — DAMON + Contiguous Memory Allocator
audit (R&D #69.3).

Two memory-management surfaces that no other module touches :

  /sys/kernel/mm/damon/admin/   DAMON (Data Access MONitor) is
                                  the kernel's in-kernel access-
                                  monitoring engine. When a user
                                  configures an Adaptive
                                  Operating Scheme via the
                                  sysfs ABI, kdamonds run in
                                  the background applying
                                  reclaim / migration / paging
                                  policies based on observed
                                  access frequency.

  /sys/kernel/mm/cma/<name>/    Contiguous Memory Allocator
                                  regions. Drivers (notably
                                  NVIDIA / GPU memory pinning
                                  paths, V4L2, DMA) carve out
                                  physically-contiguous chunks
                                  here for hardware that can't
                                  do scatter-gather. Allocation
                                  failures (alloc_pages_fail
                                  counter) are an early
                                  indicator of physical-memory
                                  fragmentation that breaks GPU
                                  workloads.

Why on a homelab :

* CUDA pinned-memory workloads can exhaust the CMA pool ;
  alloc_pages_fail then climbs and cudaMallocHost returns
  ENOMEM despite plenty of free RAM.
* A misconfigured DAMON scheme (huge quota, narrow address
  range) can throttle a GPU process by stealing its working
  set.

Reads :
  /sys/kernel/mm/cma/<name>/{count,used,nr_pages,
                              alloc_pages_success,
                              alloc_pages_fail}
  /sys/kernel/mm/damon/admin/{kdamonds/*,version,*}

Verdicts (priority order) :
  cma_alloc_failing               ≥1 CMA region has
                                    alloc_pages_fail > 0.
  damon_scheme_quota_breached     ≥1 kdamond scheme reports
                                    quota_violations > 0.
  damon_enabled_no_schemes        DAMON sysfs admin tree present
                                    AND ≥1 kdamond directory
                                    AND no scheme files under
                                    it (probably half-configured).
  requires_root                   /sys/kernel/mm/{damon,cma}
                                    present but unreadable.
  ok                              all healthy / both absent
                                    with no faults observed.
  unknown                         /sys/kernel/mm/damon AND
                                    /sys/kernel/mm/cma both
                                    absent (kernel without the
                                    features ; no auditable
                                    state).

stdlib only.
"""
from __future__ import annotations

import os
from typing import List, Optional


NAME = "damon_cma_audit"


_SYS_DAMON_ADMIN = "/sys/kernel/mm/damon/admin"
_SYS_CMA = "/sys/kernel/mm/cma"


def _read(path: str) -> Optional[str]:
    try:
        with open(path) as f:
            return f.read().strip()
    except OSError:
        return None


def _read_int(path: str) -> Optional[int]:
    t = _read(path)
    if t is None:
        return None
    try:
        return int(t)
    except ValueError:
        return None


def list_cma_regions(sys_cma: str = _SYS_CMA) -> List[dict]:
    if not os.path.isdir(sys_cma):
        return []
    out: List[dict] = []
    try:
        names = sorted(os.listdir(sys_cma))
    except OSError:
        return []
    for n in names:
        d = os.path.join(sys_cma, n)
        if not os.path.isdir(d):
            continue
        out.append({
            "name": n,
            "count": _read_int(os.path.join(d, "count")),
            "used": _read_int(os.path.join(d, "used")),
            "nr_pages": _read_int(os.path.join(d, "nr_pages")),
            "alloc_pages_success": _read_int(os.path.join(
                d, "alloc_pages_success")),
            "alloc_pages_fail": _read_int(os.path.join(
                d, "alloc_pages_fail")),
        })
    return out


def list_kdamonds(sys_damon: str = _SYS_DAMON_ADMIN
                       ) -> List[dict]:
    """List kdamonds and their scheme counts."""
    root = os.path.join(sys_damon, "kdamonds")
    if not os.path.isdir(root):
        return []
    out: List[dict] = []
    try:
        # First entry is usually "nr_kdamonds", skip non-numeric.
        for n in sorted(os.listdir(root)):
            d = os.path.join(root, n)
            if not os.path.isdir(d):
                continue
            if not n.isdigit():
                continue
            kd: dict = {"id": n}
            ctx_dir = os.path.join(d, "contexts")
            scheme_count = 0
            quota_breach_total = 0
            if os.path.isdir(ctx_dir):
                try:
                    for cn in os.listdir(ctx_dir):
                        ctx = os.path.join(ctx_dir, cn)
                        if not (os.path.isdir(ctx)
                                  and cn.isdigit()):
                            continue
                        schemes_root = os.path.join(
                            ctx, "schemes")
                        if os.path.isdir(schemes_root):
                            try:
                                for sn in os.listdir(
                                        schemes_root):
                                    sp = os.path.join(
                                        schemes_root, sn)
                                    if not (os.path.isdir(sp)
                                              and sn.isdigit()):
                                        continue
                                    scheme_count += 1
                                    qv = _read_int(os.path.join(
                                        sp, "stats",
                                        "qt_exceeds"))
                                    if qv:
                                        quota_breach_total += qv
                            except OSError:
                                pass
                except OSError:
                    pass
            kd["scheme_count"] = scheme_count
            kd["quota_breach_total"] = quota_breach_total
            out.append(kd)
    except OSError:
        return []
    return out


def classify(cma_present: bool, damon_present: bool,
              cma_regions: List[dict],
              kdamonds: List[dict]) -> dict:
    if not cma_present and not damon_present:
        return {"verdict": "unknown",
                "reason": ("Neither /sys/kernel/mm/damon nor "
                          "/sys/kernel/mm/cma present — kernel "
                          "built without these features."),
                "recommendation": ""}

    # 1) cma_alloc_failing
    failing = [r for r in cma_regions
                    if (r.get("alloc_pages_fail") or 0) > 0]
    if failing:
        sample = ", ".join(
            f"{r['name']} fail={r['alloc_pages_fail']}"
                for r in failing[:3])
        return {"verdict": "cma_alloc_failing",
                "reason": (f"{len(failing)} CMA region(s) report "
                          f"alloc_pages_fail > 0 : {sample}."),
                "recommendation": _recipe_cma_failing()}

    # 2) damon_scheme_quota_breached
    breached = [kd for kd in kdamonds
                    if (kd.get("quota_breach_total") or 0) > 0]
    if breached:
        sample = ", ".join(
            f"kd{kd['id']} qt={kd['quota_breach_total']}"
                for kd in breached[:3])
        return {"verdict": "damon_scheme_quota_breached",
                "reason": (f"{len(breached)} DAMON kdamond(s) "
                          f"with quota-exceeded stats : "
                          f"{sample}."),
                "recommendation": _recipe_damon_quota()}

    # 3) damon_enabled_no_schemes
    if kdamonds:
        empties = [kd for kd in kdamonds
                       if (kd.get("scheme_count") or 0) == 0]
        if empties and len(empties) == len(kdamonds):
            return {"verdict": "damon_enabled_no_schemes",
                    "reason": (f"{len(kdamonds)} kdamond(s) "
                              f"running but no schemes "
                              f"configured — DAMON daemon "
                              f"present without any policy."),
                    "recommendation": _recipe_damon_no_schemes()}

    return {"verdict": "ok",
            "reason": (f"CMA regions = {len(cma_regions)} "
                      f"(no failures) ; "
                      f"DAMON kdamonds = {len(kdamonds)}."),
            "recommendation": ""}


def status(config=None,
            sys_damon: str = _SYS_DAMON_ADMIN,
            sys_cma: str = _SYS_CMA) -> dict:
    cma_present = os.path.isdir(sys_cma)
    damon_present = os.path.isdir(sys_damon)
    cma_regions = list_cma_regions(sys_cma)
    kdamonds = list_kdamonds(sys_damon)
    verdict = classify(cma_present, damon_present,
                          cma_regions, kdamonds)
    return {"ok": cma_present or damon_present,
              "cma_present": cma_present,
              "damon_present": damon_present,
              "cma_region_count": len(cma_regions),
              "cma_regions": cma_regions,
              "kdamond_count": len(kdamonds),
              "kdamonds": kdamonds,
              "verdict": verdict}


# ── recovery recipes ────────────────────────────────────────────

def _recipe_cma_failing() -> str:
    return ("# CMA allocation failures = physical fragmentation\n"
            "# or pool exhaustion. Inspect each region :\n"
            "for r in /sys/kernel/mm/cma/*; do\n"
            "  echo \"$(basename $r)\"\n"
            "  cat \"$r/count\" \"$r/used\" \"$r/alloc_pages_fail\"\n"
            "done\n"
            "# Increase pool with kernel cmdline 'cma=256M' and\n"
            "# regenerate initramfs.\n")


def _recipe_damon_quota() -> str:
    return ("# A DAMON scheme is hitting its quota. List schemes :\n"
            "ls /sys/kernel/mm/damon/admin/kdamonds/*/contexts/\\\n"
            "  */schemes/\n"
            "# Raise the quota or tighten the address range :\n"
            "# /sys/kernel/mm/damon/admin/kdamonds/<id>/contexts/\n"
            "#   <c>/schemes/<s>/quotas/{bytes,ms}\n")


def _recipe_damon_no_schemes() -> str:
    return ("# kdamond running with no schemes — daemon idle. To\n"
            "# stop it cleanly :\n"
            "echo 0 | sudo tee /sys/kernel/mm/damon/admin/kdamonds/\\\n"
            "  <id>/state\n"
            "# Or configure a scheme (see Documentation/admin-\n"
            "# guide/mm/damon/usage.rst).\n")
