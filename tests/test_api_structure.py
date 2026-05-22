"""Structural tests for the api/ package.

Guards against regressions we hit while splitting api.py from a 4809-line
monolith into submodules. Without these checks, any future PR that :

  * moves a handler without updating api/__init__.py re-exports
  * leaves a stale stub of a moved handler in _monolith.py
  * patches a non-existent attribute via patch.object(api.X, "name")
  * routes a URL in server.py to a handler that doesn't exist
  * silently double-defines a handler in two submodules

would slip through CI without these guards.

These tests are pure introspection — they don't exercise behavior. They
just walk the codebase via AST and assert structural invariants.
"""
from __future__ import annotations

import ast
import importlib
import pkgutil
import re
from pathlib import Path

import pytest

from gpu_dashboard import api


REPO_ROOT = Path(__file__).resolve().parent.parent
API_DIR = REPO_ROOT / "src" / "gpu_dashboard" / "api"
SERVER_PY = REPO_ROOT / "src" / "gpu_dashboard" / "server.py"
TESTS_DIR = REPO_ROOT / "tests"


def _api_submodules() -> list:
    """Return the importable api submodules (auth, llm, power, cost, ...).
    Excludes _monolith — which is migration-state, not target architecture."""
    out: list = []
    for info in pkgutil.iter_modules([str(API_DIR)]):
        if info.name.startswith("_"):
            continue
        out.append(importlib.import_module(f"gpu_dashboard.api.{info.name}"))
    return out


def _handler_names_in_module(mod) -> list:
    """Return public handle_X functions REALLY defined in `mod` (not just
    re-imports). Excludes forwarding stubs whose body is `return _m.X(...)`."""
    src_path = getattr(mod, "__file__", None)
    forwarding: set = set()
    if src_path and Path(src_path).exists():
        try:
            tree = ast.parse(Path(src_path).read_text())
            for node in ast.iter_child_nodes(tree):
                if (isinstance(node, ast.FunctionDef)
                        and node.name.startswith("handle_")
                        and _is_forwarding_stub(node)):
                    forwarding.add(node.name)
        except (OSError, SyntaxError):
            pass
    out: list = []
    for name in dir(mod):
        if not name.startswith("handle_"):
            continue
        if name in forwarding:
            continue
        obj = getattr(mod, name)
        if not callable(obj):
            continue
        if getattr(obj, "__module__", "") == mod.__name__:
            out.append(name)
    return out


# ── 1. every handle_X in a submodule must be reachable via api.X ─────────


def test_every_submodule_handler_is_reexported():
    """If a submodule defines handle_X, api.handle_X must resolve to it.

    Without this, you split out a handler and the public surface
    (server.py, tests) silently breaks because the re-export is missing.
    """
    failures: list = []
    for mod in _api_submodules():
        for name in _handler_names_in_module(mod):
            if not hasattr(api, name):
                failures.append(f"{mod.__name__}.{name} not re-exported as api.{name}")
                continue
            via_api = getattr(api, name)
            via_mod = getattr(mod, name)
            if via_api is not via_mod:
                failures.append(
                    f"api.{name} != {mod.__name__}.{name} "
                    f"(re-export stale — likely shadowed by _monolith)"
                )
    assert not failures, "Missing or stale re-exports :\n  " + "\n  ".join(failures)


# ── 2. no handler defined in two different submodules ───────────────────


def _is_forwarding_stub(fn_node: ast.FunctionDef) -> bool:
    """True if the function body is a thin one-call delegation : either
    `return _m.X(...)` (simple form) or `from . import Y as _x ; return
    _x.X(...)` (late-import form used when forwarding to a sibling
    submodule that would otherwise cause a cycle).

    These are intentional bridging during the split, not duplicates.
    """
    body = fn_node.body
    # Simple form : single `return _m.X(...)`
    if len(body) == 1 and isinstance(body[0], ast.Return):
        val = body[0].value
        return (isinstance(val, ast.Call)
                and isinstance(val.func, ast.Attribute)
                and isinstance(val.func.value, ast.Name))
    # Late-import form : `from . import X as _foo` then `return _foo.X(...)`
    if (len(body) == 2
            and isinstance(body[0], ast.ImportFrom)
            and isinstance(body[1], ast.Return)):
        val = body[1].value
        return (isinstance(val, ast.Call)
                and isinstance(val.func, ast.Attribute)
                and isinstance(val.func.value, ast.Name))
    return False


def test_no_duplicate_handler_definitions():
    """If a handler appears defined in both _monolith AND a submodule, the
    migration left a stub behind. Forwarding stubs (body = return _m.X(...))
    are explicitly allowed since they bridge cross-module refs during
    the split."""
    seen: dict = {}
    duplicates: list = []
    for py in sorted(API_DIR.glob("*.py")):
        if py.name == "__init__.py":
            continue
        try:
            tree = ast.parse(py.read_text())
        except SyntaxError as e:
            pytest.fail(f"{py.name} fails to parse : {e}")
        for node in ast.walk(tree):
            if not (isinstance(node, ast.FunctionDef)
                    and node.name.startswith("handle_")):
                continue
            if _is_forwarding_stub(node):
                continue  # forwarding stub, by design
            if node.name in seen:
                duplicates.append(
                    f"{node.name} defined in BOTH {seen[node.name]} AND {py.name}"
                )
            else:
                seen[node.name] = py.name
    assert not duplicates, "Duplicate handler definitions :\n  " + "\n  ".join(duplicates)


# ── 3. every server.py route → real handler ─────────────────────────────


def test_server_routes_resolve_to_real_handlers():
    """server.py wires URLs to api.handle_X. Every reference must exist
    on the api package — typos / stale routes get caught here."""
    src = SERVER_PY.read_text()
    # Match api.handle_X( OR api.<submodule>.handle_X( in server.py
    refs = set(re.findall(r"\bapi\.(handle_\w+)\b", src))
    unknown = [name for name in refs if not hasattr(api, name)]
    assert not unknown, f"server.py routes to missing handlers : {unknown}"


# ── 4. test files patching api._monolith or api.<sub> point at real attrs ────


def test_patch_object_targets_exist():
    """tests/*.py uses patch.object(api._monolith, 'X') and similar.
    When a handler moves submodules, these targets break SILENTLY (the
    real implementation runs instead of the mock). This test catches
    moved-but-not-updated patch targets early.
    """
    # patch.object(api._monolith, "X")        OR
    # patch.object(api.auth, "X")             OR
    # monkeypatch.setattr(api._monolith, "X") OR
    # monkeypatch.setattr(api.power, "X")
    pattern = re.compile(
        r"\b(?:patch\.object|monkeypatch\.setattr)"
        r"\(\s*api\.(\w+)\s*,\s*['\"](\w+)['\"]"
    )
    failures: list = []
    for py in sorted(TESTS_DIR.glob("*.py")):
        if py.name == Path(__file__).name:
            continue
        for line_no, line in enumerate(py.read_text().splitlines(), 1):
            for m in pattern.finditer(line):
                target_mod_name, attr = m.group(1), m.group(2)
                target_mod = getattr(api, target_mod_name, None)
                if target_mod is None:
                    failures.append(
                        f"{py.name}:{line_no} patches api.{target_mod_name} "
                        f"but api.{target_mod_name} doesn't exist"
                    )
                    continue
                if not hasattr(target_mod, attr):
                    failures.append(
                        f"{py.name}:{line_no} patches api.{target_mod_name}.{attr} "
                        f"but {target_mod_name} has no attribute {attr!r} — "
                        "the function probably moved during refactor"
                    )
    assert not failures, "Broken patch targets :\n  " + "\n  ".join(failures)


# ── 5. every handle_X has a sane signature (ctx as first arg) ───────────


def test_handler_signatures_take_ctx_first():
    """Convention : every public handle_X takes `ctx` as first arg.
    Tests instantiate ctx dicts ; without this convention, callers break."""
    import inspect
    failures: list = []
    for mod in _api_submodules() + [api._monolith]:
        for name in dir(mod):
            if not name.startswith("handle_"):
                continue
            obj = getattr(mod, name)
            if not callable(obj):
                continue
            if getattr(obj, "__module__", "") != mod.__name__:
                continue  # re-import, skip
            try:
                params = list(inspect.signature(obj).parameters)
            except (ValueError, TypeError):
                continue
            if not params or params[0] not in ("ctx", "self"):
                failures.append(
                    f"{mod.__name__}.{name}({', '.join(params)}) — first arg "
                    f"should be 'ctx' (got {params[0] if params else '<none>'})"
                )
    assert not failures, "Handler signature violations :\n  " + "\n  ".join(failures)


# ── 6. forwarding stubs in submodules genuinely forward to _monolith ──────


def test_forwarding_stubs_resolve():
    """During the split, submodules define forwarding stubs like
    `def _gpus_available(): return _m._gpus_available()` so tests
    patching `api._monolith._gpus_available` are honored.

    If _monolith ever loses a helper that a submodule forwards, the
    forwarding stub breaks at first call. Verify they resolve at import.
    """
    failures: list = []
    monolith = api._monolith
    for mod in _api_submodules():
        # Find module-level functions whose body is just `return _m.X(...)`
        src = Path(mod.__file__).read_text()
        try:
            tree = ast.parse(src)
        except SyntaxError as e:
            pytest.fail(f"{mod.__name__} unparseable : {e}")
        for node in ast.iter_child_nodes(tree):
            if not isinstance(node, ast.FunctionDef):
                continue
            # Looking for : def X(...): return _m.X(...)
            if (len(node.body) == 1 and isinstance(node.body[0], ast.Return)
                    and isinstance(node.body[0].value, ast.Call)
                    and isinstance(node.body[0].value.func, ast.Attribute)
                    and isinstance(node.body[0].value.func.value, ast.Name)
                    and node.body[0].value.func.value.id == "_m"):
                forwarded_name = node.body[0].value.func.attr
                if not hasattr(monolith, forwarded_name):
                    failures.append(
                        f"{mod.__name__}.{node.name} forwards to "
                        f"_m.{forwarded_name} which no longer exists in _monolith"
                    )
    assert not failures, "Broken forwarding stubs :\n  " + "\n  ".join(failures)
