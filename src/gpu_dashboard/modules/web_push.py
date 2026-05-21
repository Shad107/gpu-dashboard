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
    """Load or generate the VAPID keypair. Returns {public_key, private_key, pem_path}.

    Also writes vapid_priv.pem next to vapid.json (mode 0600). Used by send_push()
    for ECDSA JWT signing (cheaper than re-encoding the b64 scalar each time).
    """
    path = os.path.join(config_dir, "vapid.json")
    pem_path = os.path.join(config_dir, "vapid_priv.pem")
    if os.path.exists(path) and os.path.exists(pem_path):
        try:
            with open(path) as f:
                data = json.load(f)
            if isinstance(data, dict) and "public_key" in data and "private_key" in data:
                data["pem_path"] = pem_path
                return data
        except (json.JSONDecodeError, OSError):
            pass

    # Generate fresh keypair AND keep the openssl PEM file for signing.
    os.makedirs(config_dir, exist_ok=True)
    r = subprocess.run(
        ["openssl", "ecparam", "-genkey", "-name", "prime256v1", "-noout", "-out", pem_path],
        capture_output=True, text=True, timeout=10,
    )
    if r.returncode != 0:
        raise RuntimeError(f"openssl ecparam failed: {r.stderr}")
    os.chmod(pem_path, 0o600)

    r2 = subprocess.run(
        ["openssl", "ec", "-in", pem_path, "-text", "-noout"],
        capture_output=True, text=True, timeout=10,
    )
    if r2.returncode != 0:
        raise RuntimeError(f"openssl ec failed: {r2.stderr}")
    priv_scalar = _extract_hex_block(r2.stdout, "priv:", "pub:")
    priv_bytes = bytes.fromhex(priv_scalar)[-32:]
    priv_b64 = _b64url_encode(priv_bytes)
    pub_hex = _extract_hex_block(r2.stdout, "pub:", "ASN1 OID:")
    pub_bytes = bytes.fromhex(pub_hex)
    if len(pub_bytes) != 65 or pub_bytes[0] != 0x04:
        raise RuntimeError(f"unexpected public key format: len={len(pub_bytes)}")
    pub_b64 = _b64url_encode(pub_bytes)

    data = {"public_key": pub_b64, "private_key": priv_b64}
    with open(path, "w") as f:
        json.dump(data, f, indent=2)
    os.chmod(path, 0o600)
    data["pem_path"] = pem_path
    return data


def _der_to_jose(der: bytes) -> bytes:
    """Convert ASN.1 DER ECDSA signature → JOSE raw 64-byte (r || s).

    DER format : 0x30 len 0x02 rlen <r bytes> 0x02 slen <s bytes>
    Both r and s may have a leading 0x00 (if MSB set) — we strip and pad to 32.
    """
    if der[0] != 0x30:
        raise ValueError("not a DER sequence")
    # Skip the SEQUENCE header. Handle 1-byte and 2-byte length forms.
    if der[1] & 0x80:
        skip = 2 + (der[1] & 0x7f)
    else:
        skip = 2
    body = der[skip:]
    # r
    if body[0] != 0x02:
        raise ValueError("expected INTEGER for r")
    rlen = body[1]
    r = body[2:2 + rlen].lstrip(b"\x00").rjust(32, b"\x00")
    body = body[2 + rlen:]
    # s
    if body[0] != 0x02:
        raise ValueError("expected INTEGER for s")
    slen = body[1]
    s = body[2:2 + slen].lstrip(b"\x00").rjust(32, b"\x00")
    return r + s


def _vapid_jwt(endpoint: str, pem_path: str, subject: str = "mailto:admin@gpu-dashboard.local") -> str:
    """Build a signed VAPID JWT (ES256). Returns header.payload.signature in base64url."""
    from urllib.parse import urlparse
    import time as _time

    parsed = urlparse(endpoint)
    audience = f"{parsed.scheme}://{parsed.netloc}"
    header = {"typ": "JWT", "alg": "ES256"}
    payload = {
        "aud": audience,
        "exp": int(_time.time()) + 12 * 3600,  # 12h validity
        "sub": subject,
    }
    h_b64 = _b64url_encode(json.dumps(header, separators=(",", ":")).encode())
    p_b64 = _b64url_encode(json.dumps(payload, separators=(",", ":")).encode())
    signing_input = f"{h_b64}.{p_b64}".encode()

    # Sign with openssl
    r = subprocess.run(
        ["openssl", "dgst", "-sha256", "-sign", pem_path],
        input=signing_input, capture_output=True, timeout=10,
    )
    if r.returncode != 0:
        raise RuntimeError(f"openssl sign failed: {r.stderr.decode(errors='replace')}")
    sig_b64 = _b64url_encode(_der_to_jose(r.stdout))
    return f"{h_b64}.{p_b64}.{sig_b64}"


def send_push(subscription: dict, vapid: dict, message: Optional[dict] = None, ttl: int = 60) -> tuple[bool, str]:
    """Send a Web Push notification to ONE subscription.

    Phase 1 (this cycle) : sends a 'tickle' — empty body, no encryption.
    The browser fires the SW 'push' event with no data ; the SW falls back to
    a generic notification message. Good enough for 'alert fired' signals.

    Phase 2 (next cycle) will add encrypted payload per RFC 8291 so the SW
    can show the alert text + GPU temp directly.

    Returns (ok, message). ok=False on HTTP error or expired subscription.
    """
    import urllib.request
    import urllib.error

    endpoint = subscription["endpoint"]
    jwt = _vapid_jwt(endpoint, vapid["pem_path"])
    headers = {
        "TTL": str(ttl),
        "Authorization": f"vapid t={jwt}, k={vapid['public_key']}",
        "Content-Length": "0",
    }
    req = urllib.request.Request(endpoint, data=b"", headers=headers, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            return True, f"{resp.status}"
    except urllib.error.HTTPError as e:
        # 404 / 410 = subscription expired ; caller should drop it
        body = e.read().decode(errors="replace")[:200] if hasattr(e, "read") else ""
        return False, f"HTTP {e.code}: {body}"
    except Exception as e:
        return False, str(e)
