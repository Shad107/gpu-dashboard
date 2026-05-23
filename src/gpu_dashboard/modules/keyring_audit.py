"""Module keyring_audit — kernel keyring quota auditor (R&D #46.4).

Reads /proc/key-users (per-UID quota) and /proc/keys (per-key
listing). The kernel limits each UID to maxkeys keys + maxbytes
descriptor bytes — defaults are tight (200 keys / 20 000 bytes
per non-root UID). Foot-guns :

  - A desktop with browsers + WireGuard + SSH agent + kerberos
    + Docker easily holds 50-100 keys per UID. The 200-key
    default leaves little headroom.
  - Stale session keyrings linger after logout/SIGKILL until
    GC ; under heavy reload they can briefly hit the limit.
  - A daemon holding sealed-secret keys (systemd-credentials,
    AF_TLS keyring) can leak on restart loops.

/proc/key-users format :
  UID: total used/refs keys/maxkeys bytes/maxbytes

Verdicts (priority-ordered) :
  uid_quota_approaching   ≥1 UID has keys ≥ 80 % of maxkeys OR
                          bytes ≥ 80 % of maxbytes.
  many_session_keyrings   /proc/keys shows the same UID owns
                          > 50 `_ses` (session) keyrings — likely
                          a session-GC leak.
  ok                      all UIDs comfortably below quota.
  unknown                 /proc/key-users unreadable (CONFIG_KEYS=n
                          or AppArmor / SELinux denying).

stdlib only.
"""
from __future__ import annotations

import os
import re
from typing import Optional


NAME = "keyring_audit"


_PROC_KEY_USERS = "/proc/key-users"
_PROC_KEYS = "/proc/keys"


_USER_LINE_RE = re.compile(
    r"^\s*(?P<uid>\d+):\s+(?P<total>\d+)\s+"
    r"(?P<used>\d+)/(?P<refs>\d+)\s+"
    r"(?P<keys>\d+)/(?P<maxkeys>\d+)\s+"
    r"(?P<bytes>\d+)/(?P<maxbytes>\d+)"
)


def _read(p: str) -> Optional[str]:
    try:
        with open(p) as f:
            return f.read()
    except OSError:
        return None


def parse_key_users(text: str) -> list:
    out: list = []
    if not text:
        return out
    for line in text.splitlines():
        m = _USER_LINE_RE.match(line)
        if not m:
            continue
        out.append({
            "uid": int(m.group("uid")),
            "total": int(m.group("total")),
            "used": int(m.group("used")),
            "refs": int(m.group("refs")),
            "keys": int(m.group("keys")),
            "maxkeys": int(m.group("maxkeys")),
            "bytes": int(m.group("bytes")),
            "maxbytes": int(m.group("maxbytes")),
        })
    return out


def parse_keys(text: str) -> list:
    """Each line : <hexid> <flags> <usage> <perm> <ts> <uid> <gid>
    <type> <desc>: <data>. We only need uid + type for the leak
    detector."""
    out: list = []
    if not text:
        return out
    for line in text.splitlines():
        parts = line.split(None, 8)
        if len(parts) < 8:
            continue
        try:
            uid = int(parts[5])
        except ValueError:
            continue
        ktype = parts[7]
        desc = parts[8] if len(parts) > 8 else ""
        out.append({"uid": uid, "type": ktype, "desc": desc})
    return out


_RECIPE_BUMP_QUOTA = (
    "# A UID is close to maxkeys / maxbytes. Bump the global\n"
    "# defaults — they apply to every non-root UID :\n"
    "echo 1000 | sudo tee /proc/sys/kernel/keys/maxkeys\n"
    "echo 100000 | sudo tee /proc/sys/kernel/keys/maxbytes\n"
    "# Persistent :\n"
    "sudo tee /etc/sysctl.d/99-keys.conf <<'EOF'\n"
    "kernel.keys.maxkeys = 1000\n"
    "kernel.keys.maxbytes = 100000\n"
    "EOF\n"
    "sudo sysctl --system"
)

_RECIPE_SESSION_LEAK = (
    "# > 50 session keyrings on the same UID — likely a logout-GC\n"
    "# leak or a daemon respawning without releasing its session.\n"
    "# Inspect :\n"
    "sudo keyctl rdescribe `keyctl show -x | grep _ses | head -20`\n"
    "# Long-term fix : reboot to clear stale sessions ; investigate\n"
    "# which service is spawning them via journalctl."
)


_QUOTA_THRESHOLD = 0.80
_SESSION_KEYRING_LIMIT = 50


def classify(users: list, keys: list) -> dict:
    if not users:
        return {"verdict": "unknown",
                "reason": ("/proc/key-users unreadable (CONFIG_KEYS=n "
                           "or AppArmor / SELinux denying)."),
                "recommendation": ""}
    approaching: list = []
    for u in users:
        mk = u.get("maxkeys") or 0
        mb = u.get("maxbytes") or 0
        if mk and u["keys"] / mk >= _QUOTA_THRESHOLD:
            approaching.append((u, "keys", u["keys"] / mk))
        elif mb and u["bytes"] / mb >= _QUOTA_THRESHOLD:
            approaching.append((u, "bytes", u["bytes"] / mb))
    if approaching:
        names = ", ".join(
            f"uid {u['uid']} {kind}={u[kind]}/"
            f"{u['max' + kind]} ({ratio:.0%})"
            for u, kind, ratio in approaching[:3])
        return {"verdict": "uid_quota_approaching",
                "reason": (f"{len(approaching)} UID(s) at ≥ 80 % "
                           f"of keyring quota. {names}"),
                "recommendation": _RECIPE_BUMP_QUOTA}
    # Session-keyring leak detector
    sess_by_uid: dict = {}
    for k in keys:
        if k.get("type") == "keyring" and "_ses" in (k.get("desc") or ""):
            sess_by_uid[k["uid"]] = sess_by_uid.get(k["uid"], 0) + 1
    leaky = [(u, c) for u, c in sess_by_uid.items()
              if c > _SESSION_KEYRING_LIMIT]
    if leaky:
        names = ", ".join(f"uid {u} = {c} session keyrings"
                            for u, c in sorted(leaky, key=lambda x: -x[1])[:3])
        return {"verdict": "many_session_keyrings",
                "reason": (f"{len(leaky)} UID(s) hold > "
                           f"{_SESSION_KEYRING_LIMIT} session "
                           f"keyrings. {names}"),
                "recommendation": _RECIPE_SESSION_LEAK}
    return {"verdict": "ok",
            "reason": (f"{len(users)} UID(s) with keyring quota ; "
                       f"none approaching the 80 % threshold."),
            "recommendation": ""}


def status(cfg=None) -> dict:
    text_u = _read(_PROC_KEY_USERS)
    text_k = _read(_PROC_KEYS) or ""
    if text_u is None:
        return {
            "ok": False,
            "verdict": {"verdict": "unknown",
                         "reason": "/proc/key-users unreadable.",
                         "recommendation": ""},
            "users": [], "key_count": 0,
        }
    users = parse_key_users(text_u)
    keys = parse_keys(text_k)
    verdict = classify(users, keys)
    return {
        "ok": True,
        "user_count": len(users),
        "key_count": len(keys),
        "users": users,
        "verdict": verdict,
    }
