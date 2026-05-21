"""Web Push (VAPID) key management.

VAPID = Voluntary Application Server Identification. Browser push providers
(Mozilla, Google, Apple) require an ECDSA P-256 keypair :
 - Public key goes to the browser via `applicationServerKey` in PushManager.subscribe()
 - Private key signs each push payload sent to the push service

We generate the keypair via openssl subprocess (already required for
nvidia-settings flow) and persist as ~/.config/gpu-dashboard/vapid.json
in base64url-encoded form (the format browsers expect).

Used by cycle 82-84 :
- 82 (this file) : key generation + GET /api/push/vapid
- 83 : POST /api/push/subscribe + DB storage
- 84 : alert_monitor wires push delivery
"""
from __future__ import annotations

import base64
import json
import os
import subprocess
import tempfile
from typing import Optional


NAME = "web_push"


def _b64url_encode(data: bytes) -> str:
    """Base64-url encode without padding (the format browsers expect)."""
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def _b64url_decode(s: str) -> bytes:
    """Inverse of _b64url_encode."""
    s = s + "=" * (-len(s) % 4)
    return base64.urlsafe_b64decode(s.encode("ascii"))


def can_enable() -> tuple[bool, str]:
    """Check whether openssl is available for keypair generation."""
    try:
        r = subprocess.run(["openssl", "version"], capture_output=True, text=True, timeout=2)
        if r.returncode == 0:
            return True, f"openssl available ({r.stdout.strip()})"
        return False, "openssl returned non-zero"
    except FileNotFoundError:
        return False, "openssl not installed"
    except Exception as e:
        return False, f"openssl probe failed: {e}"


def _generate_keypair() -> tuple[str, str]:
    """Generate a fresh ECDSA P-256 keypair via openssl. Returns (priv_b64, pub_b64).

    Both keys are encoded as base64url, no padding :
    - priv_b64 : the 32-byte private scalar
    - pub_b64  : the 65-byte uncompressed public point (0x04 || X || Y)
    """
    with tempfile.TemporaryDirectory() as tmp:
        priv_pem = os.path.join(tmp, "priv.pem")
        # Generate ECDSA P-256 key
        r = subprocess.run(
            ["openssl", "ecparam", "-genkey", "-name", "prime256v1", "-out", priv_pem, "-noout"],
            capture_output=True, text=True, timeout=10,
        )
        if r.returncode != 0:
            raise RuntimeError(f"openssl ecparam failed: {r.stderr}")

        # Extract private scalar (32 bytes) via openssl ec -text
        r = subprocess.run(
            ["openssl", "ec", "-in", priv_pem, "-text", "-noout"],
            capture_output=True, text=True, timeout=10,
        )
        if r.returncode != 0:
            raise RuntimeError(f"openssl ec failed: {r.stderr}")
        priv_scalar = _extract_hex_block(r.stdout, "priv:", "pub:")
        priv_bytes = bytes.fromhex(priv_scalar)[-32:]  # take last 32 bytes (drop leading 0 if any)
        priv_b64 = _b64url_encode(priv_bytes)

        # Extract public uncompressed point (65 bytes : 0x04 || X || Y)
        pub_hex = _extract_hex_block(r.stdout, "pub:", "ASN1 OID:")
        pub_bytes = bytes.fromhex(pub_hex)
        if len(pub_bytes) != 65 or pub_bytes[0] != 0x04:
            raise RuntimeError(f"unexpected public key format: len={len(pub_bytes)}")
        pub_b64 = _b64url_encode(pub_bytes)

        return priv_b64, pub_b64


def _extract_hex_block(text: str, start_marker: str, end_marker: str) -> str:
    """Pull a hex-encoded block out of `openssl ec -text` output.

    Format example (from openssl 3.x) :
        priv:
            00:c2:91:e7:...
            ...
        pub:
            04:b3:8f:...
            ...
        ASN1 OID: prime256v1
    """
    lines = text.splitlines()
    capture = False
    out = []
    for line in lines:
        stripped = line.strip()
        if stripped.startswith(start_marker):
            capture = True
            continue
        if capture and stripped.startswith(end_marker):
            break
        if capture:
            out.append(stripped.replace(":", ""))
    return "".join(out)


def ensure_vapid_keys(config_dir: str) -> dict:
    """Load or generate the VAPID keypair. Returns {public_key, private_key}.

    On first call : generates via openssl, writes vapid.json (0600), returns it.
    On subsequent calls : reads the file.
    Corrupted file → re-generates (only the dashboard server reads this).
    """
    path = os.path.join(config_dir, "vapid.json")
    if os.path.exists(path):
        try:
            with open(path) as f:
                data = json.load(f)
            if isinstance(data, dict) and "public_key" in data and "private_key" in data:
                return data
        except (json.JSONDecodeError, OSError):
            pass  # fall through to regen

    priv_b64, pub_b64 = _generate_keypair()
    data = {"public_key": pub_b64, "private_key": priv_b64}
    os.makedirs(config_dir, exist_ok=True)
    with open(path, "w") as f:
        json.dump(data, f, indent=2)
    os.chmod(path, 0o600)  # private key — owner-readable only
    return data
