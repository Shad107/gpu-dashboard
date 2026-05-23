"""Module nic_queue_affinity — NIC RX/TX queue + RPS/XPS auditor (R&D #40.4).

For LAN-served inference (Home Assistant + OpenWebUI + Discord bots
fanning requests in), the NIC's queue layout and per-queue CPU
affinity matters as much as the GPU's IRQ affinity (shipped #38.4
gpu_irq_affinity). The kernel exposes :

  /sys/class/net/<dev>/queues/rx-N/rps_cpus       hex CPU mask :
                                                    which CPUs are
                                                    eligible to run
                                                    softIRQ rxN.
  /sys/class/net/<dev>/queues/rx-N/rps_flow_cnt   per-queue RFS flow
                                                    table size (0 =
                                                    disabled).
  /sys/class/net/<dev>/queues/tx-N/xps_cpus       hex CPU mask : which
                                                    CPUs may select
                                                    txN for transmit.
  /sys/class/net/<dev>/queues/tx-N/byte_queue_limits/{limit_max,...}
                                                    BQL state.
  /sys/class/net/<dev>/{tx_queue_len,mtu,gro_flush_timeout,
                         napi_defer_hard_irqs,operstate,carrier}

Verdicts (per device, then worst-case across UP devices) :
  ok                                queues spread + RPS+XPS set
  single_queue_nic                  1 RX/1 TX — can't optimize
  multi_queue_no_rps                multiple RX queues but every
                                     rps_cpus mask is zero ; the
                                     box is leaving RPS unused.
  xps_single_cpu_bottleneck         every xps_cpus mask has popcount
                                     1 → all TX serialised on one
                                     CPU.
  rfs_disabled                      rps_flow_cnt=0 across all RX ;
                                     no per-flow steering on a
                                     multi-flow workload.
  rps_misaligned_with_gpu_numa      ≥1 RX queue's rps_cpus mask
                                     points at CPUs NOT in the
                                     GPU's NUMA node (cross-ref
                                     gpu_cpu_affinity #37.2).
  no_active_nic                     every device down ; nothing
                                     to audit.
  unknown                           /sys/class/net unreadable.

stdlib only.
"""
from __future__ import annotations

import os
from typing import Optional


NAME = "nic_queue_affinity"


_SYS_NET = "/sys/class/net"


_SKIP_DEVICES = ("lo", "bonding_masters")


def _read(p: str) -> Optional[str]:
    try:
        with open(p) as f:
            return f.read()
    except OSError:
        return None


def _read_int(p: str) -> Optional[int]:
    t = _read(p)
    if t is None:
        return None
    try:
        return int(t.strip())
    except ValueError:
        return None


def parse_cpu_mask(text: str) -> set:
    """Parse a hex CPU mask like '000,0000ffff' or 'ff' into a set.

    Linux exposes masks in comma-separated 32-bit hex words, LSB
    rightmost — e.g. 'ff' = CPUs 0..7, '00010000,00000000' = CPU 48.
    """
    if not text:
        return set()
    cleaned = text.strip().replace(",", "")
    if not cleaned:
        return set()
    try:
        value = int(cleaned, 16)
    except ValueError:
        return set()
    cpus: set = set()
    i = 0
    while value:
        if value & 1:
            cpus.add(i)
        value >>= 1
        i += 1
    return cpus


def list_devices(sys_net: str = _SYS_NET) -> list:
    if not os.path.isdir(sys_net):
        return []
    out: list = []
    for name in sorted(os.listdir(sys_net)):
        if name in _SKIP_DEVICES:
            continue
        ddir = os.path.join(sys_net, name)
        if not os.path.isdir(ddir):
            continue
        out.append(name)
    return out


def read_device(sys_net: str, dev: str) -> dict:
    ddir = os.path.join(sys_net, dev)
    queues_dir = os.path.join(ddir, "queues")
    rx_queues: list = []
    tx_queues: list = []
    if os.path.isdir(queues_dir):
        for q in sorted(os.listdir(queues_dir)):
            qpath = os.path.join(queues_dir, q)
            if q.startswith("rx-"):
                rps_cpus_text = (_read(os.path.join(
                    qpath, "rps_cpus")) or "").strip()
                rps_flow_cnt = _read_int(os.path.join(
                    qpath, "rps_flow_cnt"))
                rx_queues.append({
                    "name": q,
                    "rps_cpus_hex": rps_cpus_text,
                    "rps_cpus": sorted(parse_cpu_mask(rps_cpus_text)),
                    "rps_flow_cnt": rps_flow_cnt,
                })
            elif q.startswith("tx-"):
                xps_cpus_text = (_read(os.path.join(
                    qpath, "xps_cpus")) or "").strip()
                bql_limit = _read_int(os.path.join(
                    qpath, "byte_queue_limits", "limit"))
                tx_queues.append({
                    "name": q,
                    "xps_cpus_hex": xps_cpus_text,
                    "xps_cpus": sorted(parse_cpu_mask(xps_cpus_text)),
                    "bql_limit": bql_limit,
                })
    return {
        "dev": dev,
        "operstate": (_read(os.path.join(ddir, "operstate")) or "").strip(),
        "carrier": _read_int(os.path.join(ddir, "carrier")),
        "type": _read_int(os.path.join(ddir, "type")),
        "tx_queue_len": _read_int(os.path.join(ddir, "tx_queue_len")),
        "mtu": _read_int(os.path.join(ddir, "mtu")),
        "gro_flush_timeout": _read_int(
            os.path.join(ddir, "gro_flush_timeout")),
        "napi_defer_hard_irqs": _read_int(
            os.path.join(ddir, "napi_defer_hard_irqs")),
        "rx_queue_count": len(rx_queues),
        "tx_queue_count": len(tx_queues),
        "rx_queues": rx_queues,
        "tx_queues": tx_queues,
    }


def is_up(dev: dict) -> bool:
    if dev.get("operstate") == "up":
        return True
    # bridges + dummy interfaces sometimes report state="unknown" with
    # carrier=1 ; that still counts as participating in fan-out.
    return dev.get("carrier") == 1 and dev.get("operstate") != "down"


_RECIPE_RPS_FULL = (
    "# Enable RPS across all online CPUs on every RX queue of <DEV>.\n"
    "# Replace <MASK> with a hex mask covering the CPUs you want to\n"
    "# steer softIRQs to (full mask = `printf '%x\\n' $(((1<<$(nproc))-1))`).\n"
    "DEV=eth0\n"
    "MASK=$(printf '%x\\n' $(((1<<$(nproc))-1)))\n"
    "for q in /sys/class/net/$DEV/queues/rx-*; do\n"
    "  echo $MASK | sudo tee $q/rps_cpus\n"
    "done\n"
    "# Persist via /etc/udev/rules.d/99-net-rps.rules ACTION==\"add\"\n"
    "# SUBSYSTEM==\"net\" KERNEL==\"$DEV\" RUN+=\"...\""
)

_RECIPE_XPS_SPREAD = (
    "# Spread TX queues across CPUs — assign each tx-N to one CPU.\n"
    "DEV=eth0\n"
    "n=0\n"
    "for q in /sys/class/net/$DEV/queues/tx-*; do\n"
    "  printf '%x\\n' $((1 << n)) | sudo tee $q/xps_cpus\n"
    "  n=$((n+1))\n"
    "done"
)

_RECIPE_RFS = (
    "# Enable RFS — system-wide flow table + per-RX-queue slice.\n"
    "echo 32768 | sudo tee /proc/sys/net/core/rps_sock_flow_entries\n"
    "DEV=eth0\n"
    "QCNT=$(ls /sys/class/net/$DEV/queues/ | grep -c rx-)\n"
    "PERQ=$((32768 / QCNT))\n"
    "for q in /sys/class/net/$DEV/queues/rx-*; do\n"
    "  echo $PERQ | sudo tee $q/rps_flow_cnt\n"
    "done"
)


def _all_rps_zero(dev: dict) -> bool:
    rx = dev.get("rx_queues", [])
    if not rx:
        return False
    return all(not q.get("rps_cpus") for q in rx)


def _all_xps_single_cpu(dev: dict) -> bool:
    tx = dev.get("tx_queues", [])
    if not tx:
        return False
    return all(len(q.get("xps_cpus") or []) == 1 for q in tx)


def _rfs_disabled(dev: dict) -> bool:
    rx = dev.get("rx_queues", [])
    if not rx:
        return False
    return all((q.get("rps_flow_cnt") or 0) == 0 for q in rx)


def _rps_outside_gpu_numa(dev: dict, gpu_numa_cpus: set) -> bool:
    if not gpu_numa_cpus:
        return False
    for q in dev.get("rx_queues", []):
        mask = set(q.get("rps_cpus") or [])
        if mask and not mask.issubset(gpu_numa_cpus):
            return True
    return False


_RANK = {
    "ok": 0, "single_queue_nic": 1,
    "rfs_disabled": 2, "multi_queue_no_rps": 3,
    "xps_single_cpu_bottleneck": 3,
    "rps_misaligned_with_gpu_numa": 4,
}


def classify(devices: list,
              gpu_numa_cpus: Optional[set] = None) -> dict:
    if not devices:
        return {"verdict": "unknown",
                "reason": "/sys/class/net unreadable.",
                "recommendation": ""}
    up_devs = [d for d in devices if is_up(d)]
    if not up_devs:
        return {"verdict": "no_active_nic",
                "reason": "No NIC currently up with link.",
                "recommendation": ""}
    best: dict = {"verdict": "ok",
                    "reason": ("All up NICs have multi-queue + RPS + "
                               "XPS configured."),
                    "recommendation": ""}
    for d in up_devs:
        if d["rx_queue_count"] <= 1 and d["tx_queue_count"] <= 1:
            cand = "single_queue_nic"
            cand_reason = (f"{d['dev']} has only 1 RX + 1 TX queue — "
                           f"can't spread softIRQs further. Fine for "
                           f"a single-flow workload.")
            cand_recipe = ""
        elif _rps_outside_gpu_numa(d, gpu_numa_cpus or set()):
            offender = next((q["name"] for q in d["rx_queues"]
                              if set(q["rps_cpus"])
                              and not set(q["rps_cpus"]).issubset(
                                  gpu_numa_cpus or set())), "rx-?")
            cand = "rps_misaligned_with_gpu_numa"
            cand_reason = (f"{d['dev']}/{offender} steers softIRQs to "
                           f"CPUs outside the GPU's NUMA node — "
                           f"cross-NUMA cache pollution on the "
                           f"inference fan-out path.")
            cand_recipe = _RECIPE_RPS_FULL
        elif _all_rps_zero(d) and d["rx_queue_count"] > 1:
            cand = "multi_queue_no_rps"
            cand_reason = (f"{d['dev']} has {d['rx_queue_count']} RX "
                           f"queues but rps_cpus=0 on every queue — "
                           f"RPS unused. softIRQ spread depends "
                           f"entirely on IRQ-affinity rotation.")
            cand_recipe = _RECIPE_RPS_FULL
        elif _all_xps_single_cpu(d) and d["tx_queue_count"] > 1:
            cand = "xps_single_cpu_bottleneck"
            cand_reason = (f"{d['dev']} has {d['tx_queue_count']} TX "
                           f"queues but every xps_cpus mask has "
                           f"popcount 1 — TX is serialised on one "
                           f"CPU per queue with no spread.")
            cand_recipe = _RECIPE_XPS_SPREAD
        elif _rfs_disabled(d) and d["rx_queue_count"] > 1:
            cand = "rfs_disabled"
            cand_reason = (f"{d['dev']} has multi-queue RX but "
                           f"rps_flow_cnt=0 across all queues — "
                           f"per-flow steering disabled. Enable RFS "
                           f"for multi-client inference fan-out.")
            cand_recipe = _RECIPE_RFS
        else:
            cand = "ok"
            cand_reason = (f"{d['dev']} : RPS+XPS configured across "
                           f"{d['rx_queue_count']} RX / "
                           f"{d['tx_queue_count']} TX queues.")
            cand_recipe = ""
        if _RANK.get(cand, 0) > _RANK.get(best["verdict"], 0):
            best = {"verdict": cand, "reason": cand_reason,
                     "recommendation": cand_recipe}
    return best


def _try_gpu_numa_cpus(cfg) -> set:
    """Best-effort cross-reference with shipped gpu_cpu_affinity."""
    try:
        from . import gpu_cpu_affinity
        out = gpu_cpu_affinity.status(cfg)
        if not out.get("ok"):
            return set()
        # Collect the union of "local_cpus" sets across GPUs.
        cpus: set = set()
        for g in out.get("gpus") or []:
            local = g.get("local_cpus") or []
            for c in local:
                if isinstance(c, int):
                    cpus.add(c)
        return cpus
    except Exception:
        return set()


def status(cfg=None) -> dict:
    dev_names = list_devices(_SYS_NET)
    if not dev_names:
        return {
            "ok": False, "devices": [],
            "verdict": {"verdict": "unknown",
                         "reason": "/sys/class/net unreadable.",
                         "recommendation": ""},
            "gpu_numa_cpus": [],
        }
    devs = [read_device(_SYS_NET, n) for n in dev_names]
    gpu_numa_cpus = _try_gpu_numa_cpus(cfg)
    verdict = classify(devs, gpu_numa_cpus)
    return {
        "ok": True,
        "device_count": len(devs),
        "devices": devs,
        "gpu_numa_cpus": sorted(gpu_numa_cpus),
        "verdict": verdict,
    }
