"""Module net_proto_counters — TCP/UDP protocol stack auditor (R&D #44.4).

Parses /proc/net/{snmp, netstat, sockstat} and surfaces the
protocol-stack counters that point at *actual problems* on a
LAN-served inference fan-out box.

  /proc/net/snmp           Per-proto header/value pairs (Tcp,
                           Udp, Ip, Icmp). The headline numbers
                           are RetransSegs, OutRsts, RcvbufErrors,
                           NoPorts.
  /proc/net/netstat        TcpExt + IpExt extended counters :
                           ListenOverflows, ListenDrops,
                           TCPBacklogDrop, TCPAbortOnMemory,
                           TCPMemoryPressures, PFMemallocDrop,
                           TCPSpuriousRTOs.
  /proc/net/sockstat       Aggregate socket counts (TCP/UDP/RAW
                           in-use, orphan, time-wait).

Verdicts (priority-ordered) :
  listen_overflow            ListenOverflows + ListenDrops > 0 →
                             accept() queue overflowed at least
                             once ; a client tried to connect to
                             OpenWebUI / ollama / vllm and got
                             RST. Bump net.core.somaxconn +
                             tcp.max_syn_backlog.
  rcvbuf_errors              UdpRcvbufErrors > 0 (Udp drops when
                             socket receive buffer fills — bumps
                             via SO_RCVBUF / net.core.rmem_*).
  high_retrans               TCP RetransSegs / OutSegs ≥ 1 % over
                             total session (cumulative since boot
                             — coarse but cheap signal).
  tcp_memory_pressure        TCPMemoryPressures > 0 ; kernel hit
                             tcp_mem watermark, throttled SKBs.
  backlog_drops              TCPBacklogDrop or PFMemallocDrop > 0
                             — sockets dropping under memory
                             pressure or excess backlog.
  ok                         all quiet.
  unknown                    /proc/net/snmp + netstat both
                             unreadable.

stdlib only.
"""
from __future__ import annotations

import os
from typing import Optional


NAME = "net_proto_counters"


_PROC_NET_SNMP = "/proc/net/snmp"
_PROC_NET_NETSTAT = "/proc/net/netstat"
_PROC_NET_SOCKSTAT = "/proc/net/sockstat"


def _read(p: str) -> Optional[str]:
    try:
        with open(p) as f:
            return f.read()
    except OSError:
        return None


def parse_kv_file(text: str) -> dict:
    """Both snmp + netstat have the same format :

      Tcp: HeaderField1 HeaderField2 ...
      Tcp: value1 value2 ...

    so we pair odd/even lines per prefix.
    """
    if not text:
        return {}
    out: dict = {}
    lines = text.splitlines()
    headers: dict = {}
    for line in lines:
        if not line.strip() or ":" not in line:
            continue
        prefix, _, rest = line.partition(":")
        tokens = rest.split()
        if prefix not in headers:
            headers[prefix] = tokens
        else:
            try:
                values = [int(t) for t in tokens]
            except ValueError:
                continue
            if len(values) == len(headers[prefix]):
                section = out.setdefault(prefix, {})
                for k, v in zip(headers[prefix], values):
                    section[k] = v
            headers[prefix] = []  # reset for next pair
    return out


def parse_sockstat(text: str) -> dict:
    """Lines like "TCP: inuse 21 orphan 0 tw 17 alloc 37 mem 1089"."""
    out: dict = {}
    if not text:
        return out
    for line in text.splitlines():
        if ":" not in line:
            continue
        proto, _, rest = line.partition(":")
        tokens = rest.split()
        section: dict = {}
        i = 0
        while i < len(tokens) - 1:
            key = tokens[i]
            try:
                val = int(tokens[i + 1])
            except ValueError:
                i += 1
                continue
            section[key] = val
            i += 2
        if section:
            out[proto.strip()] = section
    return out


_RECIPE_LISTEN_OVERFLOW = (
    "# accept() queue overflowed — clients tried to connect and got\n"
    "# RST. Bump kernel + per-listen limits :\n"
    "echo 4096 | sudo tee /proc/sys/net/core/somaxconn\n"
    "echo 4096 | sudo tee /proc/sys/net/ipv4/tcp_max_syn_backlog\n"
    "sudo tee -a /etc/sysctl.d/99-net-listen.conf <<'EOF'\n"
    "net.core.somaxconn = 4096\n"
    "net.ipv4.tcp_max_syn_backlog = 4096\n"
    "EOF\n"
    "# Application side : pass backlog=4096 to listen() (uvicorn\n"
    "# --backlog 4096, gunicorn --backlog 4096)."
)

_RECIPE_RCVBUF = (
    "# UDP RcvbufErrors > 0 — sockets are dropping incoming packets\n"
    "# under buffer pressure. Bump :\n"
    "sudo tee -a /etc/sysctl.d/99-net-rmem.conf <<'EOF'\n"
    "net.core.rmem_default = 1048576\n"
    "net.core.rmem_max = 16777216\n"
    "EOF\n"
    "sudo sysctl --system"
)

_RECIPE_RETRANS = (
    "# High TCP retransmit rate. Common causes :\n"
    "#  - flaky cable / SFP (see shipped #43.4 nic_ring_audit for\n"
    "#    rx_crc_errors).\n"
    "#  - mismatched MTU on path (try ip link set <DEV> mtu 1500).\n"
    "#  - congestion control choice (try BBR if not already) :\n"
    "echo bbr | sudo tee /proc/sys/net/ipv4/tcp_congestion_control"
)

_RECIPE_TCP_MEM = (
    "# Kernel hit tcp_mem watermark — bump pool :\n"
    "# Compute pages (page = 4 KB) ; example for 4-16-32 GB pool :\n"
    "echo '1048576 4194304 8388608' | \\\n"
    "  sudo tee /proc/sys/net/ipv4/tcp_mem\n"
    "# Persist via /etc/sysctl.d/99-tcp-mem.conf"
)

_RECIPE_BACKLOG = (
    "# Packets dropped from kernel backlog or memalloc — bump :\n"
    "echo 50000 | sudo tee /proc/sys/net/core/netdev_max_backlog\n"
    "echo 'net.core.netdev_max_backlog = 50000' | \\\n"
    "  sudo tee /etc/sysctl.d/99-net-backlog.conf"
)


_RANK = {
    "ok": 0, "unknown": 0,
    "backlog_drops": 1, "tcp_memory_pressure": 2,
    "high_retrans": 3, "rcvbuf_errors": 4,
    "listen_overflow": 5,
}


_RETRANS_RATIO_THRESHOLD = 0.01  # 1 %
_RETRANS_MIN_SEGS = 100_000      # don't classify off tiny samples


def classify(snmp: dict, netstat: dict, sockstat: dict) -> dict:
    if not snmp and not netstat:
        return {"verdict": "unknown",
                "reason": ("/proc/net/snmp + /proc/net/netstat "
                           "both unreadable."),
                "recommendation": ""}
    tcp = snmp.get("Tcp", {})
    udp = snmp.get("Udp", {})
    tcp_ext = netstat.get("TcpExt", {})
    # 1) listen overflow (the cleanest "client got RST" signal).
    lo = (tcp_ext.get("ListenOverflows") or 0)
    ld = (tcp_ext.get("ListenDrops") or 0)
    if lo > 0 or ld > 0:
        cand = ("listen_overflow",
                 (f"ListenOverflows={lo}, ListenDrops={ld} since boot "
                  f"— accept() queue overflowed ; client(s) got RST. "
                  f"Bump net.core.somaxconn + tcp_max_syn_backlog."),
                 _RECIPE_LISTEN_OVERFLOW)
        return _to_verdict(cand)
    # 2) UDP rcvbuf errors.
    rb = udp.get("RcvbufErrors", 0)
    if rb > 0:
        cand = ("rcvbuf_errors",
                 (f"Udp.RcvbufErrors={rb} — UDP sockets are dropping "
                  f"incoming packets under buffer pressure."),
                 _RECIPE_RCVBUF)
        return _to_verdict(cand)
    # 3) high TCP retrans rate (cumulative — coarse).
    rs = tcp.get("RetransSegs", 0)
    os_ = tcp.get("OutSegs", 0)
    if (rs > 0 and os_ >= _RETRANS_MIN_SEGS
            and rs / os_ >= _RETRANS_RATIO_THRESHOLD):
        cand = ("high_retrans",
                 (f"TCP RetransSegs={rs} of OutSegs={os_} "
                  f"({rs / os_ * 100:.2f} %). Likely a flaky link, "
                  f"MTU mismatch, or congestion control mis-tune."),
                 _RECIPE_RETRANS)
        return _to_verdict(cand)
    # 4) TCP memory pressure.
    tmp = tcp_ext.get("TCPMemoryPressures", 0)
    if tmp > 0:
        cand = ("tcp_memory_pressure",
                 (f"TCPMemoryPressures={tmp} — kernel hit tcp_mem "
                  f"watermark, throttled SKB allocations."),
                 _RECIPE_TCP_MEM)
        return _to_verdict(cand)
    # 5) backlog / memalloc drops.
    bd = (tcp_ext.get("TCPBacklogDrop", 0)
            + tcp_ext.get("PFMemallocDrop", 0))
    if bd > 0:
        cand = ("backlog_drops",
                 (f"TCPBacklogDrop + PFMemallocDrop = {bd} — sockets "
                  f"dropping under memory pressure or excess backlog."),
                 _RECIPE_BACKLOG)
        return _to_verdict(cand)
    s_tcp = sockstat.get("TCP", {})
    inuse = s_tcp.get("inuse", 0)
    tw = s_tcp.get("tw", 0)
    return {"verdict": "ok",
            "reason": (f"TCP inuse={inuse} tw={tw} ; "
                       f"no listen overflow, retrans, rcvbuf, "
                       f"memory pressure, or backlog drops."),
            "recommendation": ""}


def _to_verdict(cand: tuple) -> dict:
    verdict, reason, recipe = cand
    return {"verdict": verdict, "reason": reason,
              "recommendation": recipe}


def status(cfg=None) -> dict:
    text_snmp = _read(_PROC_NET_SNMP) or ""
    text_net = _read(_PROC_NET_NETSTAT) or ""
    text_sock = _read(_PROC_NET_SOCKSTAT) or ""
    snmp = parse_kv_file(text_snmp)
    netstat = parse_kv_file(text_net)
    sockstat = parse_sockstat(text_sock)
    verdict = classify(snmp, netstat, sockstat)
    # Headline counters for UI rendering.
    tcp = snmp.get("Tcp", {})
    udp = snmp.get("Udp", {})
    tcp_ext = netstat.get("TcpExt", {})
    headline = {
        "tcp_in_segs": tcp.get("InSegs"),
        "tcp_out_segs": tcp.get("OutSegs"),
        "tcp_retrans": tcp.get("RetransSegs"),
        "tcp_active_opens": tcp.get("ActiveOpens"),
        "tcp_passive_opens": tcp.get("PassiveOpens"),
        "tcp_listen_overflows": tcp_ext.get("ListenOverflows"),
        "tcp_listen_drops": tcp_ext.get("ListenDrops"),
        "tcp_memory_pressures": tcp_ext.get("TCPMemoryPressures"),
        "tcp_backlog_drop": tcp_ext.get("TCPBacklogDrop"),
        "tcp_abort_on_memory": tcp_ext.get("TCPAbortOnMemory"),
        "udp_in_datagrams": udp.get("InDatagrams"),
        "udp_out_datagrams": udp.get("OutDatagrams"),
        "udp_rcvbuf_errors": udp.get("RcvbufErrors"),
        "udp_no_ports": udp.get("NoPorts"),
    }
    return {
        "ok": bool(snmp) or bool(netstat),
        "headline": headline,
        "sockstat": sockstat,
        "verdict": verdict,
    }
