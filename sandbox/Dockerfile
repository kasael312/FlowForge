# Sandbox image in which FlowForge executes shell commands for a run.
# The cloned repository is bind-mounted at /repo. Nothing else from the host
# is visible. Network access is controlled per run (none / allowlist proxy / bridge).
FROM python:3.12-slim

ENV DEBIAN_FRONTEND=noninteractive \
    PIP_NO_CACHE_DIR=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    MPLBACKEND=Agg \
    NODE_MAJOR=22

RUN apt-get update \
 && apt-get install -y --no-install-recommends \
        git curl ca-certificates build-essential make \
        tesseract-ocr tesseract-ocr-eng tesseract-ocr-vie \
        ffmpeg \
 && curl -fsSL https://deb.nodesource.com/setup_${NODE_MAJOR}.x | bash - \
 && apt-get install -y --no-install-recommends nodejs \
 && apt-get clean && rm -rf /var/lib/apt/lists/*

# Python data stack available to data_transform / run_python steps.
RUN pip install --no-cache-dir \
        pytest \
        pandas numpy matplotlib openpyxl pypdf python-docx \
        requests beautifulsoup4 lxml pillow

# Allowlisting egress proxy used when a run's network mode is "allowlist".
COPY proxy.py /opt/flowforge/proxy.py

# Unprivileged user; /repo is owned by it so the agent can write files.
RUN useradd -m -u 1000 runner && mkdir -p /repo /skills && chown runner:runner /repo /skills
USER runner
WORKDIR /repo

# Keep the container alive; the executor uses `docker exec` for each command.
CMD ["sleep", "infinity"]
