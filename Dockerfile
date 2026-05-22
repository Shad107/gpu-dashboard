# GreenWatts container — Linux + NVIDIA GPU dashboard.
#
# REQUIRES nvidia-container-toolkit installed on the HOST and the container
# launched with --gpus all (Docker 20.10+) or with `runtime: nvidia`
# (docker-compose). Without those, /dev/nvidia* is invisible inside the
# container and the dashboard reports "GPU offline".
#
# Build :   docker build -t greenwatts:latest .
# Run :     docker run -d --name greenwatts \
#             --gpus all \
#             --pid=host \
#             -p 9999:9999 \
#             -v greenwatts-config:/root/.config/gpu-dashboard \
#             -v greenwatts-data:/root/.local/share/gpu-dashboard \
#             greenwatts:latest
#
# --pid=host : required for cgroup-power attribution (we read /proc/<pid>/
# from other processes on the host). Without it, only the dashboard's own
# PID is visible — most power-attribution features go dark.
#
# Tiny image. We don't compile the frontend in the container — we ship the
# pre-built static assets that the repo already contains under
# src/gpu_dashboard/static/.

FROM python:3.13-slim-bookworm

# nvidia-smi must be present at runtime — it's provided by the
# nvidia-container-toolkit, mounted from the host. We don't apt-install
# anything NVIDIA-related (would conflict with the host driver).
#
# We DO need :
#   - ss (iproute2)              for R&D #11.4 service discovery
#   - lsof                       for jupyter kernel detection (R&D #8.7)
#   - psql client (postgresql-client) — optional, for pgvector probe (R&D #10.1)
#   - curl                       for healthcheck inside compose

RUN apt-get update && apt-get install -y --no-install-recommends \
        iproute2 \
        lsof \
        postgresql-client \
        curl \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Single Python dep
RUN pip install --no-cache-dir jsonschema

# Copy source last so iteration on code doesn't bust the apt cache layer
COPY src/ /app/src/
COPY README.md LICENSE /app/

ENV PYTHONPATH=/app/src
ENV DASHBOARD_BIND=0.0.0.0
ENV DASHBOARD_PORT=9999

# Healthcheck uses the R&D #11.1 endpoint
HEALTHCHECK --interval=30s --timeout=3s --start-period=20s --retries=3 \
    CMD curl -fs http://localhost:9999/healthz || exit 1

EXPOSE 9999
ENTRYPOINT ["python3", "-m", "gpu_dashboard"]
