"""R&D #12.7 — air-gap mode tests."""
import pytest
from gpu_dashboard.modules import airgap
from gpu_dashboard.config import Config


@pytest.fixture(autouse=True)
def _reset_audit():
    """Clear the in-memory audit buffer between tests."""
    airgap.clear_audit()
    yield
    airgap.clear_audit()


# ── is_enabled / lan_allowed ──────────────────────────────────────────────


def test_is_enabled_default_off():
    assert airgap.is_enabled(Config(defaults={})) is False


def test_is_enabled_with_flag():
    assert airgap.is_enabled(Config(defaults={"AIRGAP_MODE": "1"})) is True
    assert airgap.is_enabled(Config(defaults={"AIRGAP_MODE": "true"})) is True
    assert airgap.is_enabled(Config(defaults={"AIRGAP_MODE": "yes"})) is True


def test_is_enabled_none_config():
    assert airgap.is_enabled(None) is False


def test_lan_allowed_flag():
    assert airgap.lan_allowed(Config(defaults={"AIRGAP_LAN_ALLOWED": "1"})) is True
    assert airgap.lan_allowed(Config(defaults={})) is False


# ── _classify_host ────────────────────────────────────────────────────────


def test_classify_host_loopback():
    assert airgap._classify_host("127.0.0.1") == "loopback"
    assert airgap._classify_host("localhost") == "loopback"
    assert airgap._classify_host("::1") == "loopback"


def test_classify_host_lan_rfc1918():
    assert airgap._classify_host("192.168.1.50") == "lan"
    assert airgap._classify_host("10.0.0.5") == "lan"
    assert airgap._classify_host("172.16.20.100") == "lan"
    assert airgap._classify_host("169.254.1.1") == "lan"


def test_classify_host_external():
    assert airgap._classify_host("huggingface.co") == "external"
    assert airgap._classify_host("api.telegram.org") == "external"
    assert airgap._classify_host("8.8.8.8") == "external"


# ── allow_url ─────────────────────────────────────────────────────────────


def test_allow_url_disabled_mode_allows_all():
    cfg = Config(defaults={})  # not enabled
    assert airgap.allow_url(cfg, "https://huggingface.co/api/models/Qwen/x") is True
    assert airgap.allow_url(cfg, "http://localhost:8080/x") is True


def test_allow_url_enabled_blocks_external():
    cfg = Config(defaults={"AIRGAP_MODE": "1"})
    assert airgap.allow_url(cfg, "https://huggingface.co/api/models/x") is False
    # The block is recorded
    audit = airgap.get_audit()
    assert len(audit) == 1
    assert "huggingface.co" in audit[0]["url"]
    assert audit[0]["reason"] == "airgap-blocked-external"


def test_allow_url_enabled_allows_loopback():
    cfg = Config(defaults={"AIRGAP_MODE": "1"})
    assert airgap.allow_url(cfg, "http://localhost:11434/api/tags") is True
    assert airgap.allow_url(cfg, "http://127.0.0.1:9999/api/state") is True
    assert airgap.get_audit() == []


def test_allow_url_enabled_blocks_lan_by_default():
    cfg = Config(defaults={"AIRGAP_MODE": "1"})
    assert airgap.allow_url(cfg, "http://192.168.1.50/status") is False


def test_allow_url_enabled_with_lan_allowed():
    cfg = Config(defaults={"AIRGAP_MODE": "1", "AIRGAP_LAN_ALLOWED": "1"})
    assert airgap.allow_url(cfg, "http://192.168.1.50/status") is True
    # External still blocked
    assert airgap.allow_url(cfg, "https://huggingface.co/x") is False


def test_allow_url_malformed_url_blocked():
    cfg = Config(defaults={"AIRGAP_MODE": "1"})
    # urlparse rarely raises ; instead host is empty → classified external → block
    assert airgap.allow_url(cfg, "not-a-url") is False


# ── safe_urlopen ──────────────────────────────────────────────────────────


def test_safe_urlopen_blocked_returns_none():
    """When the URL is blocked, returns None without raising."""
    cfg = Config(defaults={"AIRGAP_MODE": "1"})
    result = airgap.safe_urlopen(cfg, "https://huggingface.co/x")
    assert result is None
    assert len(airgap.get_audit()) == 1


def test_safe_urlopen_unreachable_returns_none():
    """When the URL is allowed but unreachable, returns None (no exception)."""
    cfg = Config(defaults={})  # not enabled
    result = airgap.safe_urlopen(cfg, "http://127.0.0.1:1/will-not-connect", timeout=0.5)
    assert result is None


# ── get_audit / clear_audit ───────────────────────────────────────────────


def test_audit_returns_newest_first():
    cfg = Config(defaults={"AIRGAP_MODE": "1"})
    airgap.allow_url(cfg, "https://first.example.com/")
    airgap.allow_url(cfg, "https://second.example.com/")
    audit = airgap.get_audit()
    assert len(audit) == 2
    # Newest first : 'second' should be at index 0
    assert "second" in audit[0]["url"]


def test_audit_limit_clamped():
    cfg = Config(defaults={"AIRGAP_MODE": "1"})
    for i in range(10):
        airgap.allow_url(cfg, f"https://host-{i}.example.com/")
    audit = airgap.get_audit(limit=3)
    assert len(audit) == 3


def test_clear_audit_returns_count():
    cfg = Config(defaults={"AIRGAP_MODE": "1"})
    airgap.allow_url(cfg, "https://x.example.com/")
    n = airgap.clear_audit()
    assert n == 1
    assert airgap.get_audit() == []


# ── status ────────────────────────────────────────────────────────────────


def test_status_when_disabled():
    cfg = Config(defaults={})
    s = airgap.status(cfg)
    assert s["enabled"] is False
    assert s["lan_allowed"] is False
    assert s["blocked_count_total"] == 0


def test_status_includes_24h_count():
    cfg = Config(defaults={"AIRGAP_MODE": "1"})
    airgap.allow_url(cfg, "https://x.example.com/")
    s = airgap.status(cfg)
    assert s["blocked_count_24h"] == 1
    assert s["blocked_count_total"] == 1
