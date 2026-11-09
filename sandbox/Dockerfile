# Sandbox image in which FlowForge executes shell commands for a run.
# The cloned repository is bind-mounted at /repo. Nothing else from the host
# is visible. No network access is granted by default (see executor).
FROM python:3.12-slim

ENV DEBIAN_FRONTEND=noninteractive \
    PIP_NO_CACHE_DIR=1 \
    NODE_MAJOR=22

RUN apt-get update \
 && apt-get install -y --no-install-recommends \
        git curl ca-certificates build-essential make \
 && curl -fsSL https://deb.nodesource.com/setup_${NODE_MAJOR}.x | bash - \
 && apt-get install -y --no-install-recommends nodejs \
 && pip install --no-cache-dir pytest \
 && apt-get clean && rm -rf /var/lib/apt/lists/*

# Unprivileged user; /repo is owned by it so the agent can write files.
RUN useradd -m -u 1000 runner && mkdir -p /repo && chown runner:runner /repo
USER runner
WORKDIR /repo

# Keep the container alive; the executor uses `docker exec` for each command.
CMD ["sleep", "infinity"]
