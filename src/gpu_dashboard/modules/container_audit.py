"""Module container_audit — NVIDIA Container Toolkit GPU visibility audit (R&D #20.1).

"Why is my Ollama / ComfyUI / Frigate container running on CPU?" is
the most common Docker-on-GPU support question. The usual culprits :

  - container started without `--gpus=all` (host doesn't pass devices)
  - nvidia-container-toolkit not installed
  - NVIDIA_VISIBLE_DEVICES=void overrides --gpus=all
  - container running under a non-default runtime
  - the daemon's `default-runtime` is not `nvidia` but compose file
    forgot the runtime override

This module talks to the Docker engine over its UNIX socket using
only stdlib http.client, enumerates running containers, and for each
one reports :

  - has_gpu_devices  (HostConfig.DeviceRequests has nvidia)
  - has_runtime_nvidia (HostConfig.Runtime == 'nvidia')
  - visible_devices  (NVIDIA_VISIBLE_DEVICES env value)
  - nv_image_tag     (image looks GPU-aware)
  - verdict          ('gpu_ok' | 'cpu_fallback' | 'partial' | 'unknown')

stdlib only : http.client over AF_UNIX socket.
"""
from __future__ import annotations

import http.client
import json
import os
import socket
from typing import Optional


NAME = "container_audit"


DEFAULT_SOCKET = "/var/run/docker.sock"


class _UnixHTTPConnection(http.client.HTTPConnection):
    """HTTPConnection variant that talks to a UNIX domain socket."""

    def __init__(self, sock_path: str, timeout: float = 2.0):
        super().__init__("localhost", timeout=timeout)
        self._sock_path = sock_path

    def connect(self):
        s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        s.settimeout(self.timeout)
        s.connect(self._sock_path)
        self.sock = s


def docker_socket_path() -> str:
    return os.environ.get("DOCKER_HOST", "").removeprefix("unix://") or DEFAULT_SOCKET


def docker_available(sock_path: Optional[str] = None) -> bool:
    p = sock_path or docker_socket_path()
    return os.path.exists(p)


def _http_get(path: str, sock_path: Optional[str] = None,
               timeout: float = 2.0) -> Optional[dict]:
    """GET <path> over Docker UNIX socket, return parsed JSON or None."""
    p = sock_path or docker_socket_path()
    if not os.path.exists(p):
        return None
    try:
        conn = _UnixHTTPConnection(p, timeout=timeout)
        conn.request("GET", path)
        r = conn.getresponse()
        body = r.read()
        conn.close()
        if r.status != 200:
            return None
        return json.loads(body.decode("utf-8"))
    except (OSError, ConnectionRefusedError, TimeoutError,
            json.JSONDecodeError):
        return None


def list_containers(sock_path: Optional[str] = None) -> list[dict]:
    """GET /containers/json — running containers only."""
    out = _http_get("/containers/json", sock_path)
    return out if isinstance(out, list) else []


def inspect_container(cid: str, sock_path: Optional[str] = None) -> Optional[dict]:
    return _http_get(f"/containers/{cid}/json", sock_path)


def _env_get(env_list: list, key: str) -> Optional[str]:
    """Container env is a list of 'KEY=VALUE' strings."""
    if not env_list:
        return None
    for s in env_list:
        if isinstance(s, str) and s.startswith(key + "="):
            return s[len(key) + 1:]
    return None


def _has_nvidia_device_request(host_config: dict) -> bool:
    """HostConfig.DeviceRequests=[{Driver:'nvidia',Count:-1,...}]"""
    dr = host_config.get("DeviceRequests") if isinstance(host_config, dict) else None
    if not dr:
        return False
    for entry in dr:
        if not isinstance(entry, dict):
            continue
        if entry.get("Driver") == "nvidia":
            return True
        caps = entry.get("Capabilities") or []
        for cap in caps:
            if isinstance(cap, list) and any(c == "gpu" for c in cap):
                return True
    return False


def classify(detail: dict) -> dict:
    """Classify one container's GPU visibility.
    Returns {verdict, has_gpu_devices, has_runtime_nvidia,
             visible_devices, image_tag}."""
    host_cfg = detail.get("HostConfig") or {}
    config = detail.get("Config") or {}
    env = config.get("Env") or []
    image = config.get("Image") or detail.get("Image") or "?"
    runtime = host_cfg.get("Runtime") or "runc"
    has_dev = _has_nvidia_device_request(host_cfg)
    visible = _env_get(env, "NVIDIA_VISIBLE_DEVICES")
    has_rt_nv = runtime == "nvidia"
    # void / none → user explicitly disabled GPU
    user_voided = visible in ("void", "none", "")
    verdict: str
    reason: str
    if (has_dev or has_rt_nv) and not user_voided:
        verdict = "gpu_ok"
        reason = "Container has NVIDIA GPU access configured."
    elif (has_dev or has_rt_nv) and user_voided:
        verdict = "partial"
        reason = ("Container requests GPU but NVIDIA_VISIBLE_DEVICES is "
                  f"'{visible}'. GPU work falls back to CPU.")
    elif "nvidia" in image.lower() or "cuda" in image.lower():
        verdict = "cpu_fallback"
        reason = ("Image looks GPU-aware but no --gpus= or runtime: nvidia "
                  "was set. Add `--gpus=all` or the compose `deploy.resources` "
                  "block.")
    else:
        verdict = "unknown"
        reason = ("Container does not request GPU — fine if intentional, "
                  "broken if it should be GPU-accelerated.")
    return {
        "verdict": verdict,
        "reason": reason,
        "has_gpu_devices": has_dev,
        "has_runtime_nvidia": has_rt_nv,
        "visible_devices": visible,
        "image": image,
        "runtime": runtime,
    }


def status(cfg=None) -> dict:
    """Aggregate snapshot."""
    sock = docker_socket_path()
    if not docker_available(sock):
        return {
            "ok": False,
            "reason": f"Docker socket not found at {sock}",
            "containers": [],
            "docker_socket": sock,
        }
    listed = list_containers(sock)
    out: list = []
    cpu_fallbacks = 0
    for c in listed:
        cid = c.get("Id")
        if not cid:
            continue
        det = inspect_container(cid, sock)
        if det is None:
            continue
        cls = classify(det)
        out.append({
            "id": cid[:12],
            "names": c.get("Names") or [],
            "state": c.get("State"),
            **cls,
        })
        if cls["verdict"] in ("cpu_fallback", "partial"):
            cpu_fallbacks += 1
    return {
        "ok": True,
        "docker_socket": sock,
        "container_count": len(out),
        "cpu_fallback_count": cpu_fallbacks,
        "containers": out,
    }
