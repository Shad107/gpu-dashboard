"""Fleet-wide module health harness (Hardening #1).

Pivot point after R&D #112 survey closed the discovery track. This
test imports every module under gpu_dashboard.modules and, where a
`status()` callable exists with a single-default-arg signature
(``status(cfg=None)``), invokes it once and asserts:

  1. No traceback escapes the call.
  2. The return value is a ``dict``.
  3. The dict contains at least one of the canonical keys
     ``verdict`` or ``ok`` — the 5-state verdict contract.

Modules that ship a ``status()`` with a richer signature (e.g.
``status(cfg, proc_root, power_root)``) are skipped here because
fleet-wide we cannot supply meaningful arguments. They are still
exercised by their own dedicated tests.

Modules whose ``status()`` returns ``requires_root`` / ``unknown``
because the live VM lacks the surface are still considered healthy
— the contract is that they degrade gracefully, not that they
produce a non-unknown verdict.
"""
from __future__ import annotations

import importlib
import inspect
import pkgutil
from typing import Iterable

import pytest

import gpu_dashboard.modules as _modules_pkg


def _iter_module_names() -> Iterable[str]:
    for info in pkgutil.iter_modules(_modules_pkg.__path__):
        if info.ispkg:
            continue
        if info.name.startswith("_"):
            continue
        yield info.name


_MODULE_NAMES = sorted(_iter_module_names())


# Setup-helper modules legitimately do not emit a verdict — they
# manage external resources (systemd units, config files) and
# return a state dict. They are still required to import cleanly
# and return a dict, but are exempt from the verdict/ok contract.
_NON_AUDIT_SETUP_MODULES = frozenset({
    "watchdog_setup",
})


def _status_takes_zero_required_args(fn) -> bool:
    try:
        sig = inspect.signature(fn)
    except (TypeError, ValueError):
        return False
    for p in sig.parameters.values():
        if p.default is inspect.Parameter.empty and p.kind in (
                inspect.Parameter.POSITIONAL_ONLY,
                inspect.Parameter.POSITIONAL_OR_KEYWORD,
                inspect.Parameter.KEYWORD_ONLY):
            return False
    return True


def test_module_fleet_imports_cleanly():
    """Every module under gpu_dashboard.modules must import without
    raising. Catches import-time path probes that escape OSError."""
    failures = []
    for name in _MODULE_NAMES:
        try:
            importlib.import_module(f"gpu_dashboard.modules.{name}")
        except Exception as e:  # noqa: BLE001 — diagnostic only
            failures.append(f"{name}: {type(e).__name__}: {e}")
    assert not failures, (
        "Module import failures:\n" + "\n".join(failures))


@pytest.mark.parametrize("modname", _MODULE_NAMES)
def test_module_status_does_not_crash(modname):
    """Where the module exposes a zero-required-arg ``status()``,
    call it and assert the return contract."""
    mod = importlib.import_module(f"gpu_dashboard.modules.{modname}")
    status = getattr(mod, "status", None)
    if not callable(status):
        pytest.skip(f"{modname} has no status() callable")
    if not _status_takes_zero_required_args(status):
        pytest.skip(
            f"{modname}.status requires positional args — exercised "
            f"by its own dedicated test")
    try:
        out = status()
    except Exception as e:  # noqa: BLE001 — diagnostic only
        pytest.fail(
            f"{modname}.status() raised {type(e).__name__}: {e}")
    assert isinstance(out, dict), (
        f"{modname}.status() returned {type(out).__name__}, "
        f"expected dict")
    if modname in _NON_AUDIT_SETUP_MODULES:
        return
    assert ("verdict" in out) or ("ok" in out), (
        f"{modname}.status() dict lacks both 'verdict' and 'ok' "
        f"keys: {sorted(out.keys())[:10]}")
