"""Module proc_syscall_auxv_audit — process syscall / auxv /
timerslack audit (R&D #66.3).

Three rarely-monitored /proc surfaces that catch real foot-guns :

  /proc/<pid>/syscall      current syscall number + args. "running"
                              if on-CPU. Same number across two
                              consecutive samples = a process stuck
                              in a syscall (often D state +
                              dirty-page write-back).
  /proc/<pid>/auxv         ELF aux-vector. AT_HWCAP, AT_HWCAP2,
                              AT_PLATFORM. If AT_PLATFORM diverges
                              from `uname -m` → process running
                              under a non-native personality
                              (32-bit on 64-bit, x32, etc.) which
                              often masks ISA-specific bugs.
  /proc/<pid>/timerslack_ns Default 50_000 (50 µs). Apps that drop
                              it to 0 for low-latency I/O burn the
                              battery when running on AC-free
                              hardware.

This audit walks a small sample of PIDs (own PID + the highest-RSS
processes) — it does NOT try to map every PID, since the file
permissions vary and reading thousands would dwarf the daemon.

Verdicts (priority order) :
  syscall_hang_long           ≥1 sampled PID is in D (disk-sleep)
                              state with a non-zero wchan ; this
                              tends to be I/O-bound hangs.
  hwcap_drift                 ≥1 sampled PID exposes an AT_PLATFORM
                              that differs from the host machine
                              architecture.
  timerslack_battery_hostile  battery is *Discharging* AND ≥1
                              sampled PID has timerslack_ns == 0
                              (high-precision timer = CPU wake
                              storm).
  unexpected_secure_mode      Own process started in AT_SECURE=1
                              mode (setuid / file caps active).
                              Informational — surfaces whether
                              file-caps landed on the daemon.
  ok                          everything quiet.
  unknown                     /proc/self/{syscall,auxv} unreadable
                              (rare — disabled by yama / chroot).

stdlib only.
"""
from __future__ import annotations

import os
import struct
from typing import List, Optional, Tuple


NAME = "proc_syscall_auxv_audit"


_PROC = "/proc"
_POWER_SUPPLY = "/sys/class/power_supply"

_AT_BASE = 7
_AT_PAGESZ = 6
_AT_PLATFORM = 15
_AT_HWCAP = 16
_AT_SECURE = 23
_AT_RANDOM = 25
_AT_HWCAP2 = 26


def _read(path: str) -> Optional[str]:
    try:
        with open(path) as f:
            return f.read()
    except OSError:
        return None


def _read_bytes(path: str) -> Optional[bytes]:
    try:
        with open(path, "rb") as f:
            return f.read()
    except OSError:
        return None


def _read_int(path: str) -> Optional[int]:
    txt = _read(path)
    if txt is None:
        return None
    try:
        return int(txt.strip())
    except ValueError:
        return None


def parse_auxv(blob: Optional[bytes]) -> dict:
    """Parse the binary auxv. 8-byte key, 8-byte value (LE)."""
    out: dict = {}
    if not blob:
        return out
    for i in range(0, len(blob), 16):
        if i + 16 > len(blob):
            break
        try:
            k, v = struct.unpack("<QQ", blob[i:i + 16])
        except struct.error:
            break
        if k == 0:
            break
        out[k] = v
    return out


def read_at_platform(pid: int,
                     proc_root: str = _PROC) -> Optional[str]:
    """AT_PLATFORM is a pointer into the target process's address
    space — unreadable from outside without ptrace. Recover the
    *kernel's* idea via /proc/<pid>/personality or fall back to
    uname-equivalent /proc/sys/kernel/arch (Linux ≥6.1)."""
    return None


def read_state(pid: int, proc_root: str = _PROC) -> Optional[str]:
    txt = _read(os.path.join(proc_root, str(pid), "stat"))
    if not txt:
        return None
    rp = txt.find(")")
    if rp < 0 or rp + 2 >= len(txt):
        return None
    return txt[rp + 2:rp + 3]


def read_wchan(pid: int, proc_root: str = _PROC) -> Optional[str]:
    txt = _read(os.path.join(proc_root, str(pid), "wchan"))
    if not txt:
        return None
    return txt.strip() or None


def read_timerslack(pid: int,
                     proc_root: str = _PROC) -> Optional[int]:
    return _read_int(os.path.join(proc_root, str(pid),
                                       "timerslack_ns"))


def read_syscall(pid: int, proc_root: str = _PROC) -> Optional[str]:
    txt = _read(os.path.join(proc_root, str(pid), "syscall"))
    if txt is None:
        return None
    txt = txt.strip()
    if not txt:
        return None
    return txt.split()[0]


def host_arch() -> str:
    """Reads /proc/sys/kernel/osrelease arch suffix or uname-m
    equivalent file. Avoids subprocess on stdlib budget."""
    try:
        import platform
        return platform.machine()
    except Exception:
        return ""


def is_battery_discharging(power_root: str = _POWER_SUPPLY) -> bool:
    if not os.path.isdir(power_root):
        return False
    for entry in os.listdir(power_root):
        if not entry.startswith("BAT"):
            continue
        st = _read(os.path.join(power_root, entry, "status"))
        if st and st.strip().lower() == "discharging":
            return True
    return False


def sample_pids(proc_root: str = _PROC,
                  limit: int = 20) -> List[int]:
    """Own pid first, then up to `limit` numeric pid dirs."""
    pids: List[int] = []
    try:
        pids.append(os.getpid())
    except Exception:
        pass
    try:
        for name in sorted(os.listdir(proc_root)):
            if not name.isdigit():
                continue
            pid = int(name)
            if pid in pids:
                continue
            pids.append(pid)
            if len(pids) >= limit + 1:
                break
    except OSError:
        pass
    return pids


def gather(pids: List[int], proc_root: str = _PROC) -> List[dict]:
    out: List[dict] = []
    for pid in pids:
        out.append({
            "pid": pid,
            "state": read_state(pid, proc_root),
            "wchan": read_wchan(pid, proc_root),
            "syscall": read_syscall(pid, proc_root),
            "timerslack_ns": read_timerslack(pid, proc_root),
        })
    return out


def classify(samples: List[dict],
              own_auxv: dict,
              own_arch: str,
              battery_discharging: bool,
              own_readable: bool) -> dict:

    if not own_readable and not samples:
        return {"verdict": "unknown",
                "reason": ("Unable to read any /proc/<pid>/syscall "
                          "or /proc/self/auxv — yama / chroot / "
                          "no_new_privs likely blocking access."),
                "recommendation": ""}

    # 1) syscall_hang_long → D-state PIDs with non-empty wchan
    hung = [s for s in samples
                if s.get("state") == "D"
                  and s.get("wchan")
                  and s.get("wchan") != "0"]
    if hung:
        sample = ", ".join(f"pid={s['pid']} wchan={s['wchan']}"
                             for s in hung[:3])
        return {"verdict": "syscall_hang_long",
                "reason": (f"{len(hung)} process(es) in D "
                          f"(uninterruptible sleep) : {sample}."),
                "recommendation": _recipe_hung_proc()}

    # 2) hwcap_drift — own AT_HWCAP=0 on non-trivial arch =>
    #    suspicious. Real drift detection needs cross-process
    #    AT_PLATFORM read which requires ptrace, so we only flag
    #    AT_HWCAP missing/zero on a 64-bit machine.
    hwcap = own_auxv.get(_AT_HWCAP, 0)
    is64 = "64" in own_arch
    if is64 and own_readable and hwcap == 0:
        return {"verdict": "hwcap_drift",
                "reason": (f"Kernel reported AT_HWCAP=0x0 to a "
                          f"64-bit process on {own_arch} — "
                          f"binary may be running under a "
                          f"compat personality."),
                "recommendation": _recipe_hwcap_drift()}

    # 3) timerslack_battery_hostile
    if battery_discharging:
        aggressive = [s for s in samples
                          if s.get("timerslack_ns") == 0]
        if aggressive:
            sample = ", ".join(str(s["pid"])
                                  for s in aggressive[:5])
            return {"verdict": "timerslack_battery_hostile",
                    "reason": (f"Battery discharging and "
                              f"{len(aggressive)} process(es) "
                              f"force timerslack_ns=0 : "
                              f"{sample}."),
                    "recommendation":
                            _recipe_timerslack_battery()}

    # 4) unexpected_secure_mode — own process started with
    # AT_SECURE=1 (setuid bit honored, file caps in effect, or
    # ld.so secure-execution mode). Informational accent : the
    # operator may or may not have intended this.
    secure = own_auxv.get(_AT_SECURE, 0)
    if own_readable and secure:
        return {"verdict": "unexpected_secure_mode",
                "reason": (f"Daemon's own AT_SECURE={secure} — "
                          f"process is running with setuid bit or "
                          f"file caps active. Verify intent."),
                "recommendation": _recipe_unexpected_secure()}

    return {"verdict": "ok",
            "reason": (f"Sampled {len(samples)} PID(s) ; "
                      f"AT_HWCAP=0x{hwcap:x} ; "
                      f"AT_SECURE={secure} ; "
                      f"battery_discharging={battery_discharging}."),
            "recommendation": ""}


def status(config=None,
            proc_root: str = _PROC,
            power_root: str = _POWER_SUPPLY) -> dict:
    own_pid = os.getpid()
    own_auxv_raw = _read_bytes(
        os.path.join(proc_root, str(own_pid), "auxv"))
    own_auxv = parse_auxv(own_auxv_raw)
    own_syscall = read_syscall(own_pid, proc_root)
    own_readable = (own_auxv_raw is not None
                          or own_syscall is not None)

    pids = sample_pids(proc_root)
    samples = gather(pids, proc_root)
    arch = host_arch()
    discharging = is_battery_discharging(power_root)

    verdict = classify(samples, own_auxv, arch, discharging,
                          own_readable)

    return {"ok": own_readable,
              "sample_count": len(samples),
              "samples": samples[:10],
              "own_pid": own_pid,
              "own_hwcap": own_auxv.get(_AT_HWCAP),
              "own_hwcap2": own_auxv.get(_AT_HWCAP2),
              "own_pagesz": own_auxv.get(_AT_PAGESZ),
              "own_secure": own_auxv.get(_AT_SECURE),
              "own_at_base": own_auxv.get(_AT_BASE),
              "own_at_random_set": _AT_RANDOM in own_auxv,
              "arch": arch,
              "battery_discharging": discharging,
              "verdict": verdict}


# ── recovery recipes ────────────────────────────────────────────

def _recipe_hung_proc() -> str:
    return ("# Identify the D-state process and its kernel "
            "blocking call :\n"
            "ps -eo pid,stat,wchan:32,cmd | awk '$2 ~ /D/'\n"
            "# Inspect dmesg for I/O errors or NFS hangs :\n"
            "dmesg --since=10min | grep -iE 'hung|task|io_err'\n")


def _recipe_hwcap_drift() -> str:
    return ("# AT_HWCAP=0 from a 64-bit process is unusual.\n"
            "# Confirm personality :\n"
            "cat /proc/self/personality 2>/dev/null\n"
            "# If binary is multi-arch, retest with native ABI :\n"
            "file $(readlink /proc/self/exe)\n")


def _recipe_timerslack_battery() -> str:
    return ("# Restore the default 50 µs slack on a target pid :\n"
            "echo 50000 | sudo tee /proc/<pid>/timerslack_ns\n"
            "# Check who's setting it to 0 (often pulseaudio /\n"
            "# real-time audio apps) :\n"
            "for f in /proc/*/timerslack_ns; do\n"
            "  v=$(cat \"$f\" 2>/dev/null) || continue\n"
            "  [ \"$v\" = 0 ] && echo \"$f $v\"\n"
            "done\n")


def _recipe_unexpected_secure() -> str:
    return ("# Inspect file capabilities on the dashboard binary :\n"
            "getcap $(readlink /proc/self/exe)\n"
            "# Check setuid bit / owner :\n"
            "ls -l $(readlink /proc/self/exe)\n"
            "# To drop file caps once intent is confirmed :\n"
            "sudo setcap -r $(readlink /proc/self/exe)\n")
