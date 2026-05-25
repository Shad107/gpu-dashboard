"""Module keyring_lifecycle_audit — kernel keyring GC + persist
+ uid-namespace leak detector (R&D #98.4).

The existing keyring_audit (R&D #46.4) covers per-uid maxkeys
/ maxbytes quota and session-keyring count leaks. Three
adjacent lifecycle surfaces it does NOT touch :

  /proc/sys/kernel/keys/gc_delay                # GC interval
  /proc/sys/kernel/keys/persistent_keyring_expiry  # TTL
  /proc/keys  (uid not in /etc/passwd)           # ns leak

Foot-guns :

  - gc_delay tuned absurdly high (> 3600 s) — revoked / expired
    keyrings linger; tmux/screen reattach can race them.
  - persistent_keyring_expiry = 0 — persistent user keyrings
    never expire ; long-running homelab boxes accumulate
    months of session-keyrings.
  - /proc/keys lists a keyring owned by a uid with no entry
    in /etc/passwd (often a container leaked into host
    namespace from podman / docker / lxc).

Reads :

  /proc/sys/kernel/keys/{gc_delay, persistent_keyring_expiry,
                          maxkeys, maxbytes, root_maxkeys,
                          root_maxbytes}
  /proc/keys                                     # uid column
  /etc/passwd                                    # uid lookup

Verdicts (worst-first) :

  ns_leak_unknown_uid          warn   /proc/keys shows a uid
                                      not in /etc/passwd —
                                      container leak.
  persistent_keyring_no_expiry warn   persistent_keyring_expiry
                                      = 0 — persistent rings
                                      live forever.
  gc_delay_too_high            accent gc_delay > 3600 s.
  ok                                  GC + expiry sane.
  requires_root                       /proc/keys mode-700.
  unknown                             /proc/sys/kernel/keys
                                      absent (CONFIG_KEYS=n).

stdlib only.
"""
from __future__ import annotations

import os
from typing import Optional

NAME = "keyring_lifecycle_audit"

DEFAULT_KEYS_SYSCTL = "/proc/sys/kernel/keys"
DEFAULT_PROC_KEYS = "/proc/keys"
DEFAULT_PASSWD = "/etc/passwd"

_GC_DELAY_MAX = 3600
# Well-known service uids that legitimately won't be in
# /etc/passwd (NSS may resolve them via sssd / systemd-userdb
# without an /etc/passwd row). Skip these to avoid noise.
_SYSTEM_UID_MAX = 999


def _read_text(path: str) -> Optional[str]:
    try:
        with open(path, "r", encoding="utf-8") as fh:
            return fh.read()
    except (OSError, PermissionError):
        return None


def _read_int(path: str) -> Optional[int]:
    t = _read_text(path)
    if t is None:
        return None
    try:
        return int(t.strip())
    except ValueError:
        return None


def parse_etc_passwd_uids(text: Optional[str]) -> set:
    """Return set of uids present in /etc/passwd."""
    out: set = set()
    if not text:
        return out
    for line in text.splitlines():
        parts = line.split(":")
        if len(parts) < 3:
            continue
        try:
            out.add(int(parts[2]))
        except ValueError:
            continue
    return out


def parse_proc_keys_uids(text: Optional[str]) -> set:
    """Return set of uids that own at least one keyring."""
    out: set = set()
    if not text:
        return out
    for line in text.splitlines():
        parts = line.split(None, 8)
        if len(parts) < 8:
            continue
        try:
            out.add(int(parts[5]))
        except ValueError:
            continue
    return out


def classify(keys_sysctl_present: bool,
             gc_delay: Optional[int],
             persistent_expiry: Optional[int],
             keys_uids: set,
             passwd_uids: set,
             keys_readable: bool) -> dict:
    if not keys_sysctl_present:
        return {"verdict": "unknown",
                "reason": (
                    "/proc/sys/kernel/keys absent — kernel "
                    "built without CONFIG_KEYS.")}
    if not keys_readable:
        return {"verdict": "requires_root",
                "reason": (
                    "/proc/keys unreadable — re-run as "
                    "root.")}

    # warn — uid in /proc/keys but not in /etc/passwd
    leaked = sorted(
        uid for uid in keys_uids
        if uid >= _SYSTEM_UID_MAX
        and uid not in passwd_uids
        and uid != 65534)   # nobody
    if leaked:
        return {
            "verdict": "ns_leak_unknown_uid",
            "reason": (
                f"{len(leaked)} uid(s) own keyrings but "
                f"aren't in /etc/passwd: {leaked[:5]}. "
                "Often a containerised process leaked into "
                "the host user namespace.")}

    # warn — persistent keyring never expires
    if persistent_expiry == 0:
        return {
            "verdict": "persistent_keyring_no_expiry",
            "reason": (
                "kernel.keys.persistent_keyring_expiry=0 — "
                "persistent user keyrings live forever ; "
                "long-running boxes leak.")}

    # accent — gc_delay too high
    if gc_delay is not None and gc_delay > _GC_DELAY_MAX:
        return {
            "verdict": "gc_delay_too_high",
            "reason": (
                f"kernel.keys.gc_delay={gc_delay}s "
                f"(> {_GC_DELAY_MAX}s) — revoked / expired "
                "keyrings linger far too long.")}

    return {"verdict": "ok",
            "reason": (
                f"gc_delay={gc_delay}s ; persistent_expiry="
                f"{persistent_expiry}s ; "
                f"{len(keys_uids)} uid(s) own keyrings, "
                "all in /etc/passwd.")}


def status(config: Optional[dict] = None,
           keys_sysctl: str = DEFAULT_KEYS_SYSCTL,
           proc_keys: str = DEFAULT_PROC_KEYS,
           passwd_file: str = DEFAULT_PASSWD) -> dict:
    keys_sysctl_present = os.path.isdir(keys_sysctl)
    gc_delay = (
        _read_int(os.path.join(keys_sysctl, "gc_delay"))
        if keys_sysctl_present else None)
    persistent_expiry = (
        _read_int(os.path.join(
            keys_sysctl, "persistent_keyring_expiry"))
        if keys_sysctl_present else None)

    keys_text = _read_text(proc_keys)
    keys_readable = keys_text is not None
    keys_uids = parse_proc_keys_uids(keys_text)
    passwd_uids = parse_etc_passwd_uids(
        _read_text(passwd_file))

    verdict = classify(
        keys_sysctl_present, gc_delay, persistent_expiry,
        keys_uids, passwd_uids, keys_readable)
    return {
        "ok": verdict["verdict"] == "ok",
        "gc_delay": gc_delay,
        "persistent_keyring_expiry": persistent_expiry,
        "uid_count": len(keys_uids),
        "verdict": verdict,
    }
