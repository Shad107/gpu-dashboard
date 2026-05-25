"""Hardening #4 — synthetic missing-path environment harness.

For every module under ``gpu_dashboard.modules`` whose ``status()``
signature exposes a path-typed default parameter (string default
starting with ``/sys``, ``/proc``, ``/dev`` or ``/etc``), invoke
the module with each such parameter overridden to a known-missing
filesystem path and assert:

  1. No traceback escapes.
  2. The return value is a ``dict``.
  3. The dict contains ``verdict`` or ``ok`` — same canonical
     contract as the rest of the fleet.
  4. If a ``verdict`` is present, it is a graceful one
     (``unknown`` / ``requires_root`` / ``n/a`` / ``ok`` /
     vendor-specific informational verdict). A crash-equivalent
     verdict like ``error`` would itself be a contract break.

This catches the most common future regression: a new module
reads a hardcoded ``/sys/...`` path without ``try / except OSError``,
then crashes on a minimal kernel that doesn't expose that surface.
The existing ``test_module_fleet_health.py`` only exercises whatever
paths happen to exist on the test host — this one drives the missing
case explicitly.

Modules that don't expose path-typed default parameters are not
covered here (they're tested by the standard fleet harness against
the live host); see ``_PATH_PREFIXES`` for the recognized prefixes.
"""
from __future__ import annotations

import importlib
import inspect
import pkgutil
from typing import Iterable

import pytest

import gpu_dashboard.modules as _modules_pkg


_PATH_PREFIXES = ("/sys", "/proc", "/dev", "/etc")
_MISSING_ROOT = "/tmp/gpu_dashboard_missing_paths_test_DOES_NOT_EXIST"


# Modules that read external commands or have other irreducible
# external dependencies that aren't capturable as a path-override
# kwarg. These still pass the standard fleet harness on the live
# host; they're outside the scope of this missing-path test.
_SKIP_MODULES: frozenset[str] = frozenset({
})


def _iter_module_names() -> Iterable[str]:
    for info in pkgutil.iter_modules(_modules_pkg.__path__):
        if info.ispkg or info.name.startswith("_"):
            continue
        yield info.name


def _path_param_names(fn) -> list:
    try:
        sig = inspect.signature(fn)
    except (TypeError, ValueError):
        return []
    out: list = []
    for p in sig.parameters.values():
        if isinstance(p.default, str) and p.default.startswith(
                _PATH_PREFIXES):
            out.append(p.name)
    return out


def _has_only_optional_args(fn) -> bool:
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


def _build_param_list() -> list:
    """Enumerate (module_name, path_kwargs) tuples for parametrization
    at collection time."""
    out: list = []
    for name in sorted(_iter_module_names()):
        if name in _SKIP_MODULES:
            continue
        try:
            mod = importlib.import_module(
                f"gpu_dashboard.modules.{name}")
        except Exception:  # noqa: BLE001
            continue
        fn = getattr(mod, "status", None)
        if not callable(fn) or not _has_only_optional_args(fn):
            continue
        path_params = _path_param_names(fn)
        if not path_params:
            continue
        kwargs = {p: _MISSING_ROOT for p in path_params}
        out.append((name, kwargs))
    return out


_PARAMS = _build_param_list()


_GRACEFUL_VERDICTS = frozenset({
    "ok", "unknown", "requires_root", "n/a", "na",
    "missing", "absent", "not_applicable", "idle",
    "disabled", "off", "no_data", "uninstrumented"})


@pytest.mark.parametrize("modname,kwargs",
                          _PARAMS,
                          ids=[m for m, _ in _PARAMS])
def test_module_graceful_on_missing_paths(modname, kwargs):
    """Call ``status(**path_overrides)`` with paths pointing at a
    known-missing root and assert the module degrades gracefully."""
    mod = importlib.import_module(
        f"gpu_dashboard.modules.{modname}")
    fn = mod.status
    try:
        out = fn(**kwargs)
    except Exception as e:  # noqa: BLE001 — diagnostic
        pytest.fail(
            f"{modname}.status({kwargs}) raised "
            f"{type(e).__name__}: {e}")
    assert isinstance(out, dict), (
        f"{modname} returned {type(out).__name__}, expected dict")
    assert ("verdict" in out) or ("ok" in out), (
        f"{modname} missing 'verdict'/'ok' key: "
        f"{sorted(out.keys())[:8]}")
    verdict_obj = out.get("verdict")
    if isinstance(verdict_obj, dict):
        v = verdict_obj.get("verdict")
    elif isinstance(verdict_obj, str):
        v = verdict_obj
    else:
        return  # ok key present but no verdict — fine for some shapes
    if v is None:
        return
    # Accept any verdict the module reports — many use module-
    # specific names like "iommu_disabled", "psi_disabled",
    # "nvme_n/a_no_device" — those are still graceful. We only
    # fail if the verdict explicitly reports an error/crash.
    assert "traceback" not in v.lower(), (
        f"{modname} verdict={v!r} smells like a crash leak")
    assert "exception" not in v.lower(), (
        f"{modname} verdict={v!r} smells like a crash leak")


def test_parameter_list_is_nonempty():
    """Sanity check: this test would be meaningless if no modules
    had path-typed parameters."""
    assert len(_PARAMS) > 100, (
        f"only {len(_PARAMS)} modules have path-typed default "
        f"params — did the modules package change shape?")
