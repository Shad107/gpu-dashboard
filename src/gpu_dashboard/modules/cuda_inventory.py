"""Module cuda_inventory — CUDA toolkit inventory + collision detector (R&D #22.5).

The most common forum question for new LLM-rig owners is "why does
PyTorch report libcudart.so not found / wrong version?". The shipped
compat matrix (#18.2) answers the question from the "installed
toolkit vs driver" side. This module answers from the *user-visible*
side : where each `libcudart.so` lives on disk, what version it is,
and whether multiple CUDA installs are competing.

Common collision sources :
  - distro `nvidia-cuda-toolkit` (system /usr/lib)
  - `/usr/local/cuda-XX.Y` (NVIDIA runfile installer)
  - conda env `lib/libcudart.so` (bundled per env)
  - `LD_LIBRARY_PATH` pinning a stale version

Walks well-known roots and parses `version.json` (NVIDIA toolkit) or
`libcudart.so.<major>.<minor>.<patch>` symlinks (conda) for versions.

stdlib only.
"""
from __future__ import annotations

import json
import os
import re
from typing import Optional


NAME = "cuda_inventory"


# Roots to walk (best effort — only enumerated if they exist)
TOOLKIT_ROOTS = [
    "/usr/local",
    "/opt",
]

CONDA_ENV_ROOTS = [
    "~/anaconda3/envs",
    "~/miniconda3/envs",
    "~/mambaforge/envs",
    "~/miniforge3/envs",
    "~/.conda/envs",
]


_LIBCUDART_RE = re.compile(r"libcudart\.so(\.\d+){0,3}$")
_CUDART_VERSIONED_RE = re.compile(r"libcudart\.so\.(\d+)(?:\.(\d+))?(?:\.(\d+))?")


def expand(p: str) -> str:
    return os.path.expanduser(p)


def find_cuda_toolkits(roots: Optional[list[str]] = None) -> list[dict]:
    """Find /usr/local/cuda* dirs (or alt roots). Returns
    [{path, version, source}]."""
    out: list[dict] = []
    for root in (roots or TOOLKIT_ROOTS):
        if not os.path.isdir(root):
            continue
        try:
            for name in sorted(os.listdir(root)):
                if not name.startswith("cuda"):
                    continue
                p = os.path.join(root, name)
                if not os.path.isdir(p):
                    continue
                version = _version_from_toolkit_root(p) or _version_from_name(name)
                out.append({
                    "path": p,
                    "version": version,
                    "source": "toolkit",
                })
        except OSError:
            continue
    return out


def _version_from_toolkit_root(root: str) -> Optional[str]:
    """Parse version.json (preferred) or version.txt."""
    vj = os.path.join(root, "version.json")
    try:
        with open(vj) as f:
            d = json.load(f)
        cuda = d.get("cuda") or {}
        return cuda.get("version")
    except (OSError, json.JSONDecodeError):
        pass
    vt = os.path.join(root, "version.txt")
    try:
        with open(vt) as f:
            line = f.read().strip()
        m = re.search(r"(\d+\.\d+(?:\.\d+)?)", line)
        if m:
            return m.group(1)
    except OSError:
        pass
    return None


def _version_from_name(name: str) -> Optional[str]:
    m = re.search(r"cuda-?(\d+(?:\.\d+)?)", name)
    return m.group(1) if m else None


def find_conda_cuda(conda_roots: Optional[list[str]] = None) -> list[dict]:
    """Walk conda env dirs looking for lib/libcudart.so.* files."""
    out: list[dict] = []
    for cr in (conda_roots or CONDA_ENV_ROOTS):
        expanded = expand(cr)
        if not os.path.isdir(expanded):
            continue
        try:
            for env_name in sorted(os.listdir(expanded)):
                env_path = os.path.join(expanded, env_name)
                if not os.path.isdir(env_path):
                    continue
                lib_dir = os.path.join(env_path, "lib")
                if not os.path.isdir(lib_dir):
                    continue
                found = _find_cudart_in_dir(lib_dir)
                for entry in found:
                    out.append({
                        "path": env_path,
                        "version": entry,
                        "source": f"conda-env:{env_name}",
                    })
        except OSError:
            continue
    return out


def _find_cudart_in_dir(d: str) -> list[str]:
    """Look for libcudart.so.<X>.<Y>.<Z>, return list of versions found."""
    versions: set[str] = set()
    try:
        for name in os.listdir(d):
            m = _CUDART_VERSIONED_RE.match(name)
            if m:
                v = ".".join(filter(None, m.groups()))
                if v.count(".") >= 1:  # at least major.minor
                    versions.add(v)
    except OSError:
        pass
    return sorted(versions)


def parse_ld_library_path() -> list[dict]:
    """LD_LIBRARY_PATH entries that point at CUDA-relevant dirs."""
    raw = os.environ.get("LD_LIBRARY_PATH", "")
    if not raw:
        return []
    out: list[dict] = []
    for entry in raw.split(":"):
        if not entry or not os.path.isdir(entry):
            continue
        # Match anything that looks cuda-ish
        if "cuda" in entry.lower() or _find_cudart_in_dir(entry):
            versions = _find_cudart_in_dir(entry)
            out.append({
                "path": entry,
                "versions": versions,
                "source": "LD_LIBRARY_PATH",
            })
    return out


def detect_collisions(installs: list[dict]) -> list[dict]:
    """Group installs by major.minor; flag when 2+ installs share a
    major OR multiple distinct majors are present (likely confusing)."""
    by_major: dict[str, list[dict]] = {}
    for i in installs:
        v = i.get("version")
        if not v:
            continue
        major = v.split(".")[0]
        by_major.setdefault(major, []).append(i)
    collisions: list[dict] = []
    for major, items in by_major.items():
        if len(items) > 1:
            collisions.append({
                "kind": "multiple_same_major",
                "major": major,
                "count": len(items),
                "paths": [it["path"] for it in items],
            })
    if len(by_major) > 1:
        collisions.append({
            "kind": "multiple_majors_present",
            "majors": sorted(by_major.keys()),
        })
    return collisions


def status(cfg=None) -> dict:
    """Aggregate snapshot."""
    toolkits = find_cuda_toolkits()
    conda = find_conda_cuda()
    ld = parse_ld_library_path()
    all_installs = toolkits + conda
    collisions = detect_collisions(all_installs)
    return {
        "ok": True,
        "toolkits": toolkits,
        "conda_envs": conda,
        "ld_library_path": ld,
        "install_count": len(all_installs),
        "collisions": collisions,
        "collision_count": len(collisions),
        "verdict": _verdict(all_installs, collisions),
    }


def _verdict(installs: list[dict], collisions: list[dict]) -> dict:
    if not installs:
        return {"verdict": "none",
                "reason": ("No CUDA toolkit found in /usr/local/cuda*, "
                           "/opt/cuda*, or any conda env. Install one if "
                           "you plan to compile or develop CUDA code.")}
    if not collisions:
        return {"verdict": "clean",
                "reason": (f"{len(installs)} CUDA install(s) found, no "
                           "version collisions detected.")}
    bad = any(c.get("kind") == "multiple_majors_present" for c in collisions)
    if bad:
        return {"verdict": "version_conflict",
                "reason": ("Multiple CUDA major versions installed. "
                           "Library loader picks the first match in "
                           "LD_LIBRARY_PATH — explicitly pin the version "
                           "in your conda/venv if PyTorch errors out.")}
    return {"verdict": "duplicate",
            "reason": ("Multiple installs of the same CUDA major. "
                       "Usually harmless — but if PyTorch reports "
                       "version mismatch, check which one is on PATH first.")}
