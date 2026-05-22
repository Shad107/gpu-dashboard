# Auto-restart watchdog (`/readyz` + systemd)

R&D #11.1 added two probe endpoints :

- `GET /healthz` — sub-millisecond liveness probe. Returns 200 if the process is alive.
- `GET /readyz[?strict=1]` — full readiness probe. Returns 503 if any of `sampler` / `storage` / `nvidia` is unhealthy (and ECC / drift if strict).

These let you wire an **auto-restart watchdog** : poll `/readyz` every minute and restart the service if it fails.

## Easiest path : the dashboard does it for you

From the dashboard UI :

**Settings → Services → 🐕 Auto-restart watchdog → Enable**

Behind the scenes the dashboard writes two systemd user-level units to `~/.config/systemd/user/` and starts the timer. Zero shell needed.

You can also trigger this via the API :

```bash
# Enable
curl -X POST http://localhost:9999/api/watchdog/enable

# Disable
curl -X POST http://localhost:9999/api/watchdog/disable

# Status
curl http://localhost:9999/api/watchdog/status
```

The watchdog runs **every 60 s**. If `/readyz` returns non-2xx for > 5 s (HTTP timeout), it runs `systemctl --user restart gpu-dashboard.service`. Audit log records every restart.

## Manual path (if you'd rather configure it yourself)

### 1. Service unit (the watchdog itself)

`~/.config/systemd/user/gpu-dashboard-watchdog.service`

```ini
[Unit]
Description=Restart gpu-dashboard if /readyz fails
After=gpu-dashboard.service

[Service]
Type=oneshot
ExecStart=/bin/sh -c 'curl -fs --max-time 5 http://localhost:9999/readyz >/dev/null || systemctl --user restart gpu-dashboard.service'
```

### 2. Timer (the schedule)

`~/.config/systemd/user/gpu-dashboard-watchdog.timer`

```ini
[Unit]
Description=Run readyz check every minute

[Timer]
OnBootSec=2min
OnUnitActiveSec=60s

[Install]
WantedBy=timers.target
```

### 3. Activate

```bash
systemctl --user daemon-reload
systemctl --user enable --now gpu-dashboard-watchdog.timer
```

### 4. Inspect

```bash
# When did it last run ?
systemctl --user list-timers gpu-dashboard-watchdog

# What did it do ?
journalctl --user -u gpu-dashboard-watchdog -n 20

# Stop it
systemctl --user disable --now gpu-dashboard-watchdog.timer
```

## Strict mode (datacenter / mission-critical rigs)

Pass `?strict=1` to fail readiness on ECC errors and recent driver/kernel drift. In the watchdog ExecStart :

```
ExecStart=/bin/sh -c 'curl -fs --max-time 5 http://localhost:9999/readyz?strict=1 >/dev/null || systemctl --user restart gpu-dashboard.service'
```

WARNING : strict mode will restart the service after every `apt upgrade` that bumps the nvidia driver (last_drift age < 24h). This may be desired (force re-init of NVML) or annoying. Pick based on your rig's tolerance.

## Beyond systemd : k8s / uptime-kuma / others

The endpoints follow standard conventions, so any of these work :

### Kubernetes / k3s

```yaml
livenessProbe:
  httpGet: { path: /healthz, port: 9999 }
  initialDelaySeconds: 10
  periodSeconds: 10
readinessProbe:
  httpGet: { path: /readyz, port: 9999 }
  initialDelaySeconds: 30
  periodSeconds: 30
```

### Uptime Kuma

- **Monitor type** : HTTP(s) - JSON Query
- **URL** : `http://your-rig.local:9999/readyz`
- **JSON Query** : `$.ready`
- **Expected value** : `true`
- **Interval** : 60 s
- Connect to your existing notification channel (the dashboard's own Notif Hub if you want it eats its own dog food)

### Healthchecks.io (self-hosted)

Combine the existing R&D #6.2 deadman heartbeat (outbound) with `/healthz` (inbound). Setup in Settings → Alerts → Healthchecks. No watchdog needed — the absence of a heartbeat triggers the alert.

### Generic shell loop

```bash
until curl -fs http://localhost:9999/readyz >/dev/null; do
  echo "Dashboard not ready, waiting..."
  sleep 5
done
echo "Dashboard ready."
```

Useful in startup scripts that depend on the dashboard being live.
