"""Module service_discovery — auto-detect well-known LLM/RAG/GPU services
running on this host (R&D #11.4).

Approach :
  1. Parse `ss -tlnp` for listening TCP ports + owning PID
  2. Match (port, cmdline) against a signature library
  3. Best-effort HTTP probe on the canonical health path of each
  4. Return enriched cards : {service, port, pid, cmdline, health, version}

Signatures are pure JSON in code (community-extensible via PR).
stdlib only — subprocess + urllib.

Use cases :
  - Pre-fill the LLM monitor + vector DB watchdog config automatically
  - Display 'detected services' card so the user knows what's running
  - Detect 'unknown service running on port X' for security awareness
"""
from __future__ import annotations

import os
import re
import subprocess
import urllib.error
import urllib.request
from typing import Optional


NAME = "service_discovery"

# Signature library : {service_name : {port_hints, cmdline_patterns, health_path}}
# port_hints : list of "typical" ports (informational)
# cmdline_patterns : regex(es) matched against /proc/<pid>/cmdline
# health_path : URL path to GET for liveness check (None if not HTTP)
_SIGNATURES = [
    {
        "name": "ollama",
        "ports": [11434],
        "cmdline": [r"\bollama\b"],
        "health_path": "/api/tags",
        "category": "llm-server",
    },
    {
        "name": "llama.cpp server",
        "ports": [8080, 8081, 8000],
        "cmdline": [r"\bllama-server\b", r"server.*-m\s+\S+\.gguf"],
        "health_path": "/health",
        "category": "llm-server",
    },
    {
        "name": "vLLM",
        "ports": [8000, 8001],
        "cmdline": [r"vllm\.entrypoints\.openai\.api_server", r"\bvllm\b"],
        "health_path": "/health",
        "category": "llm-server",
    },
    {
        "name": "Text Generation WebUI",
        "ports": [7860, 5000],
        "cmdline": [r"text-generation-webui", r"server\.py.*--listen"],
        "health_path": "/api/v1/model",
        "category": "llm-ui",
    },
    {
        "name": "ComfyUI",
        "ports": [8188],
        "cmdline": [r"\bcomfy\b", r"comfyui", r"main\.py.*comfy"],
        "health_path": "/system_stats",
        "category": "diffusion",
    },
    {
        "name": "Stable Diffusion WebUI",
        "ports": [7860],
        "cmdline": [r"stable-diffusion-webui", r"launch\.py"],
        "health_path": "/sdapi/v1/options",
        "category": "diffusion",
    },
    {
        "name": "ChromaDB",
        "ports": [8000],
        "cmdline": [r"chromadb", r"chroma\b"],
        "health_path": "/api/v1/heartbeat",
        "category": "vector-db",
    },
    {
        "name": "Qdrant",
        "ports": [6333],
        "cmdline": [r"qdrant"],
        "health_path": "/",
        "category": "vector-db",
    },
    {
        "name": "Weaviate",
        "ports": [8080],
        "cmdline": [r"weaviate"],
        "health_path": "/v1/.well-known/ready",
        "category": "vector-db",
    },
    {
        "name": "JupyterLab / Notebook",
        "ports": [8888, 8889, 8890],
        "cmdline": [r"jupyter-lab", r"jupyter-notebook", r"jupyter_server"],
        "health_path": "/api/status",
        "category": "lab",
    },
    {
        "name": "Triton Inference Server",
        "ports": [8000, 8001, 8002],
        "cmdline": [r"tritonserver"],
        "health_path": "/v2/health/ready",
        "category": "llm-server",
    },
    {
        "name": "Open WebUI",
        "ports": [3000, 8080],
        "cmdline": [r"open-webui", r"openwebui"],
        "health_path": "/health",
        "category": "llm-ui",
    },
    {
        "name": "gpu-dashboard (self)",
        "ports": [9999],
        "cmdline": [r"gpu_dashboard"],
        "health_path": "/healthz",
        "category": "self",
    },
]


def parse_ss_output(text: str) -> list:
    """Parse `ss -tlnp` table → list of {port, pid, name}.

    Lines look like :
      LISTEN 0 512  0.0.0.0:8080   0.0.0.0:*   users:(("llama-server",pid=1785,fd=16))
    """
    out: list = []
    for line in text.splitlines()[1:]:  # skip header
        parts = line.split()
        if len(parts) < 4 or parts[0] != "LISTEN":
            continue
        # Local Address:Port is in column 3 (0-indexed)
        local = parts[3]
        # Extract port from '0.0.0.0:8080' / '127.0.0.1:11434' / '[::1]:6333'
        m = re.search(r":(\d+)$", local)
        if not m:
            continue
        port = int(m.group(1))
        # Process info at end : users:((<name>,pid=<n>,fd=<n>))
        pid = None
        name = ""
        if "users:" in line:
            pm = re.search(r'users:\(\("([^"]+)",pid=(\d+)', line)
            if pm:
                name = pm.group(1)
                pid = int(pm.group(2))
        out.append({"port": port, "pid": pid, "name": name})
    return out


def read_cmdline(pid: Optional[int]) -> str:
    """Read /proc/<pid>/cmdline, null bytes → spaces. Empty string on error."""
    if pid is None:
        return ""
    try:
        with open(f"/proc/{pid}/cmdline", "rb") as f:
            return f.read().decode("utf-8", errors="replace").replace("\x00", " ").strip()
    except (OSError, PermissionError):
        return ""


def match_signature(port: int, cmdline: str) -> Optional[dict]:
    """Return the signature dict that matches a listening port, or None."""
    for sig in _SIGNATURES:
        # Port hint match (one of the typical ports)
        port_match = port in sig.get("ports", [])
        # Cmdline pattern match
        cmd_match = any(re.search(pat, cmdline, re.IGNORECASE) for pat in sig.get("cmdline", []))
        # Accept if BOTH port + cmd match (high confidence), or just cmd (port-agnostic)
        if cmd_match and (port_match or True):  # cmd match alone is enough
            return sig
    return None


def probe_health(port: int, path: str, host: str = "127.0.0.1",
                 timeout: float = 1.5) -> dict:
    """HTTP GET the health path. Returns {ok, status, ms} or {ok: false}."""
    url = f"http://{host}:{port}{path}"
    try:
        import time as _time
        t0 = _time.monotonic()
        with urllib.request.urlopen(url, timeout=timeout) as r:
            status = r.status
        ms = int((_time.monotonic() - t0) * 1000)
        return {"ok": 200 <= status < 400, "status": status, "ms": ms}
    except urllib.error.HTTPError as e:
        return {"ok": False, "status": e.code, "ms": None}
    except (urllib.error.URLError, OSError, TimeoutError):
        return {"ok": False, "status": None, "ms": None}


def discover(probe: bool = True) -> dict:
    """Run full discovery. Returns aggregated dict + per-service cards.

    Services with the same PID (e.g. Qdrant listens on HTTP 6333 + gRPC 6334)
    are grouped into ONE card with `ports: [...]`, `primary_port` and `health`
    probed only on the signature's canonical port (the first match)."""
    try:
        r = subprocess.run(["ss", "-tlnp"], capture_output=True, text=True, timeout=2)
    except (FileNotFoundError, subprocess.SubprocessError, OSError):
        return {"ok": True, "available": False, "reason": "ss command unavailable"}
    if r.returncode != 0:
        return {"ok": True, "available": False, "reason": "ss failed"}

    listeners = parse_ss_output(r.stdout)
    by_pid: dict = {}    # pid → {service info, ports: [...]}
    unknown: list = []
    for L in listeners:
        port = L["port"]
        pid = L["pid"]
        cmdline = read_cmdline(pid)
        if L.get("name") in ("sshd", "systemd-resolve", "cups-browsed", "cupsd"):
            continue
        sig = match_signature(port, cmdline)
        if sig is None:
            if port not in (22, 53, 631, 5353, 1716):
                unknown.append({
                    "port": port,
                    "pid": pid,
                    "proc_name": L.get("name") or "unknown",
                    "cmdline_preview": cmdline[:80] if cmdline else "",
                })
            continue
        # Group by PID — same process, multiple ports → one card
        key = pid if pid is not None else f"port-{port}"
        if key not in by_pid:
            by_pid[key] = {
                "service": sig["name"],
                "category": sig["category"],
                "pid": pid,
                "proc_name": L.get("name") or "unknown",
                "ports": [],
                "primary_port": None,
            }
        by_pid[key]["ports"].append(port)
        # Primary port = first port that matches the signature's port_hints
        if by_pid[key]["primary_port"] is None and port in sig.get("ports", []):
            by_pid[key]["primary_port"] = port

    # Probe health on each card's primary_port (or first port if no primary)
    services: list = []
    for card in by_pid.values():
        card["ports"].sort()
        if card["primary_port"] is None and card["ports"]:
            card["primary_port"] = card["ports"][0]
        if probe and card["primary_port"]:
            # Lookup signature again for health_path
            sig = match_signature(card["primary_port"], read_cmdline(card["pid"]))
            if sig and sig.get("health_path"):
                card["health"] = probe_health(card["primary_port"], sig["health_path"])
        services.append(card)
    return {
        "ok": True,
        "available": True,
        "services_count": len(services),
        "services": services,
        "unknown_count": len(unknown),
        "unknown_listeners": unknown[:20],
    }
