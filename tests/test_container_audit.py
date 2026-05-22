"""R&D #20.1 — NVIDIA Container Toolkit GPU audit tests."""
import pytest
from unittest.mock import patch
from gpu_dashboard.modules import container_audit as ca


# ── _env_get ───────────────────────────────────────────────────────────


def test_env_get_present():
    env = ["PATH=/x", "NVIDIA_VISIBLE_DEVICES=all", "HOME=/y"]
    assert ca._env_get(env, "NVIDIA_VISIBLE_DEVICES") == "all"


def test_env_get_missing():
    assert ca._env_get(["PATH=/x"], "NVIDIA_VISIBLE_DEVICES") is None


def test_env_get_empty():
    assert ca._env_get([], "ANY") is None


def test_env_get_value_with_equals():
    env = ["FOO=bar=baz"]
    assert ca._env_get(env, "FOO") == "bar=baz"


# ── _has_nvidia_device_request ─────────────────────────────────────────


def test_has_nvidia_driver_field():
    hc = {"DeviceRequests": [{"Driver": "nvidia", "Count": -1}]}
    assert ca._has_nvidia_device_request(hc) is True


def test_has_nvidia_via_capabilities():
    hc = {"DeviceRequests": [{"Capabilities": [["gpu", "compute"]]}]}
    assert ca._has_nvidia_device_request(hc) is True


def test_no_device_requests():
    assert ca._has_nvidia_device_request({"DeviceRequests": []}) is False
    assert ca._has_nvidia_device_request({}) is False


def test_non_nvidia_driver():
    hc = {"DeviceRequests": [{"Driver": "tpu"}]}
    assert ca._has_nvidia_device_request(hc) is False


# ── classify ───────────────────────────────────────────────────────────


def _detail(image="ubuntu", env=None, runtime="runc", device_requests=None):
    return {
        "HostConfig": {
            "Runtime": runtime,
            "DeviceRequests": device_requests,
        },
        "Config": {"Image": image, "Env": env or []},
    }


def test_classify_gpu_ok_via_device_request():
    d = _detail(image="ollama/ollama",
                 device_requests=[{"Driver": "nvidia", "Count": -1}])
    c = ca.classify(d)
    assert c["verdict"] == "gpu_ok"
    assert c["has_gpu_devices"] is True


def test_classify_gpu_ok_via_runtime():
    d = _detail(image="cuda-app", runtime="nvidia")
    c = ca.classify(d)
    assert c["verdict"] == "gpu_ok"
    assert c["has_runtime_nvidia"] is True


def test_classify_partial_void_env():
    d = _detail(image="cuda", runtime="nvidia",
                 env=["NVIDIA_VISIBLE_DEVICES=void"])
    c = ca.classify(d)
    assert c["verdict"] == "partial"
    assert c["visible_devices"] == "void"


def test_classify_cpu_fallback_gpu_image():
    d = _detail(image="nvidia/cuda:11.8.0-base")
    c = ca.classify(d)
    assert c["verdict"] == "cpu_fallback"
    assert "--gpus=all" in c["reason"]


def test_classify_unknown_for_plain_image():
    d = _detail(image="postgres")
    c = ca.classify(d)
    assert c["verdict"] == "unknown"


def test_classify_returns_image_and_runtime():
    d = _detail(image="ollama/ollama", runtime="nvidia")
    c = ca.classify(d)
    assert c["image"] == "ollama/ollama"
    assert c["runtime"] == "nvidia"


# ── status ─────────────────────────────────────────────────────────────


def test_status_no_socket(tmp_path, monkeypatch):
    monkeypatch.setattr(ca, "docker_socket_path",
                        lambda: str(tmp_path / "nope.sock"))
    s = ca.status()
    assert s["ok"] is False
    assert "not found" in s["reason"]


def test_status_empty_when_no_containers():
    with patch.object(ca, "docker_available", return_value=True):
        with patch.object(ca, "list_containers", return_value=[]):
            s = ca.status()
    assert s["ok"] is True
    assert s["container_count"] == 0
    assert s["cpu_fallback_count"] == 0


def test_status_aggregates_cpu_fallbacks():
    with patch.object(ca, "docker_available", return_value=True):
        with patch.object(ca, "list_containers", return_value=[
            {"Id": "a" * 64, "Names": ["/ollama"], "State": "running"},
            {"Id": "b" * 64, "Names": ["/redis"], "State": "running"},
        ]):
            with patch.object(ca, "inspect_container",
                              side_effect=[
                                  _detail(image="nvidia/cuda"),
                                  _detail(image="redis"),
                              ]):
                s = ca.status()
    assert s["container_count"] == 2
    assert s["cpu_fallback_count"] == 1


# ── docker_socket_path ─────────────────────────────────────────────────


def test_docker_socket_default(monkeypatch):
    monkeypatch.delenv("DOCKER_HOST", raising=False)
    assert ca.docker_socket_path() == ca.DEFAULT_SOCKET


def test_docker_socket_from_env(monkeypatch):
    monkeypatch.setenv("DOCKER_HOST", "unix:///custom/docker.sock")
    assert ca.docker_socket_path() == "/custom/docker.sock"
