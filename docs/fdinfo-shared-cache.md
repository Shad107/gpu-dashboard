# Shared /proc/<pid>/fdinfo cache — Hardening #15 design

## Why this is queued, not shipped

Four modules currently walk `/proc/<pid>/fdinfo/*` independently:

| Module                          | Cost on this VM | Marker        |
|---------------------------------|-----------------|---------------|
| bpf_program_inventory_audit     | ~353 ms         | EXPECTED_SLOW |
| inotify_audit                   | ~405 ms         | EXPECTED_SLOW |
| drm_fdinfo_engine_usage_audit   | ~312 ms         | (under budget)|
| fdinfo_kinds_audit              | ~50–100 ms est. | (small)       |

Combined ≈ 1100 ms of intrinsic cost during a full `collection_profile_audit`
run. A shared cache could cut this to one walk + four parses — savings
≈ 750 ms.

Why we are not shipping the refactor in H15:

1. **Verdict is already honest.** After H10's `optimizable_total_ms`
   split, the fleet returns `verdict=ok` on this host with 2403 ms
   optimizable / 5018 ms intrinsic. The 5000 ms budget is comfortably
   not exceeded by optimizable cost. Saving 750 ms is a perf win, not
   a correctness fix.

2. **Four-module refactor risk.** Each module has subtly different
   parse needs (see Shape below). Touching four production code paths
   for a perf improvement that doesn't affect a verdict warrants a
   triggering event we don't currently have (user-reported slowness,
   fdinfo-walker count growing past four, or a 600+ module fleet).

3. **Premature abstraction.** Shipping the cache utility *without*
   refactoring the callers would leave dead code in the tree — exactly
   the pattern CLAUDE.md warns against. Shipping it *with* the refactor
   is too large a one-shot diff.

So: design only here; refactor when one of the trigger conditions
fires.

## Cache shape

```python
# src/gpu_dashboard/modules/_proc_fd_cache.py (proposed)

from __future__ import annotations
import os
import time
from typing import Dict, Optional, Tuple

_CACHE: Tuple[float, Dict[str, dict]] = (0.0, {})
_DEFAULT_TTL_S = 1.0


def scan_proc_fd(proc_root: str = "/proc",
                   ttl_s: float = _DEFAULT_TTL_S) -> Dict[str, dict]:
    """Return a per-PID snapshot of /proc/<pid>/{fd, fdinfo}.

    Cached for ttl_s seconds across modules within the same Python
    process. Result shape:

        {pid: {"uid": int_or_None,
                "fd_links": [(fd_str, target_or_None), ...],
                "fdinfo": {fd_str: text_or_None}}}

    All reads are best-effort; unreadable entries appear as None.
    Modules that only need fdinfo text can ignore fd_links; modules
    that need symlink targets (fdinfo_kinds_audit) read them too.
    """
    global _CACHE
    now = time.monotonic()
    ts, data = _CACHE
    if proc_root == "/proc" and now - ts < ttl_s and data:
        return data
    result: Dict[str, dict] = {}
    try:
        pids = [n for n in os.listdir(proc_root) if n.isdigit()]
    except OSError:
        return {}
    for pid in pids:
        ...  # walk /proc/<pid>/fd and /proc/<pid>/fdinfo
    if proc_root == "/proc":
        _CACHE = (now, result)
    return result
```

Key decisions:

- **TTL ≥ collection-cycle duration.** A 1 s TTL is enough to dedupe
  the four walks within a single `collection_profile_audit` run
  (~8 s wall-clock total, but the four walkers fire in <100 ms of each
  other once their `status()` is invoked).
- **Cache only on default `proc_root`.** Tests using `tmp_path` /
  monkeypatched roots bypass the cache to keep test isolation.
- **Lazy fields.** `fd_links` and `fdinfo` are both eagerly populated
  because all four modules need at least one. Splitting into two
  caches (links vs fdinfo) is overkill for four callers.
- **Best-effort everywhere.** EACCES on /proc/<pid>/{fd, fdinfo} is
  normal (other UID, hidden by hidepid mount). Result entries
  reflect what we could read; downstream verdicts already handle
  partial data.

## Refactor plan (deferred)

1. Create `modules/_proc_fd_cache.py` per Cache shape above.
2. Add `tests/test_proc_fd_cache.py` covering: cache hit, TTL expiry,
   tmp_path bypass, partial-readability handling.
3. Rewrite each walker's scan function to consume the cache. Preserve
   the existing return shape so the module's classify/status code
   doesn't change.
4. Remove `EXPECTED_SLOW` from `bpf_program_inventory_audit` and
   `inotify_audit` if their measured cost falls under 500 ms (per
   H16 queued task).
5. Update docs/hardening-log.md with measured before/after numbers.

## Trigger conditions to revisit

Re-open this work when any of:

- A fifth fdinfo-walker module is added (cost compounds).
- A user reports collection time noticeably slower than displayed
  in the Settings card (e.g. >12 s on a desktop with many open
  files).
- Optimizable cost crosses 4000 ms on a typical fleet (close enough
  to the 5000 ms budget that a single new module could tip it over).
